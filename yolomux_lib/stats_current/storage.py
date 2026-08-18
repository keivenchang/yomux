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
from yolomux_lib.filesystem.io_ops import read_json_file
from yolomux_lib.infra import common
from yolomux_lib.infra.filesystem_preflight import preflight_mutable_roots
from yolomux_lib.infra.host_partition import host_partitioned_state_dir

from . import identity
from . import resolution as stats_resolution


# Keep the existing YOST identity and advance beyond legacy schema 4. That
# combination makes the legacy writer's read-only header fence stop before it
# can reinterpret or mutate this intentionally incompatible schema.
APPLICATION_ID = 0x594F5354
SCHEMA_VERSION = 7
MIN_WRITER_PROTOCOL = 24
# Build 4 moves recurring CPU/GPU host sampling into statsd.  An older daemon
# must be retired rather than silently accepting context it cannot collect.
MIN_WRITER_BUILD = 4
# How long original facts stay on disk. Retention and the GUI's longest display
# window (stats_resolution.MAX_RANGE_SECONDS) are two independent knobs that used
# to be spelled with the same literal, which invited the assumption that moving
# one moved the other. They are ordered by one invariant, enforced in
# _require_retention_covers_display_window and asserted by the retention tests:
#
#     RETENTION_SECONDS >= stats_resolution.MAX_RANGE_SECONDS
#
# Below that, a 24h chart asks for buckets whose source rows were already pruned
# and draws the truncated remainder as though the window were complete -- stale
# data wearing a current timestamp. Two days keeps the whole 24h window plus a
# day of slack for late-arriving history, clock skew, and a missed nightly prune.
RETENTION_SECONDS = 2 * 24 * 60 * 60
WAL_AUTOCHECKPOINT_PAGES = 1000
# Automatic checkpoints recycle logical frames but do not necessarily shrink
# the file. This connection policy bounds the retained allocation after a reset;
# Store.vacuum() separately truncates the rewrite-sized allocation immediately.
WAL_ALLOCATION_CEILING_BYTES = 8 * 1024 * 1024
DATABASE_FILENAME = f"stats-v{SCHEMA_VERSION}.sqlite3"
# Sidecar beside the database, like WRITER_FENCE_FILENAME. The nightly prune
# schedule needs the last successful prune to survive a restart, and schema_meta
# cannot grow a column without a SCHEMA_VERSION bump that would strand the
# operator's existing history.
PRUNE_STATE_FILENAME = "stats-prune.json"


def default_socket_path(state_dir: Path | None = None) -> Path:
    """The one place the statsd socket path is decided.

    A Unix socket cannot live on a network filesystem, and the state root may be
    an NFS-exported home, so the socket belongs on the host-local, boot-scoped
    runtime root.

    The legacy probe is the coexistence half of THE SECOND RULE: a server from
    the previous build is serving right now on the old path, and a client that
    looked only at the new location would report a live service as missing. Once
    nothing is listening there, new runs move on by themselves.
    """

    root = Path(state_dir or common.STATE_DIR)
    legacy = root / "services" / SOCKET_FILENAME
    if legacy.exists():
        return legacy
    database_path = default_database_path(root).resolve(strict=False)
    database_digest = hashlib.sha256(str(database_path).encode("utf-8")).hexdigest()[:16]
    return common.RUNTIME_DIR / "services" / f"{SOCKET_FILENAME.removesuffix('.sock')}.{database_digest}.sock"


def default_database_path(state_dir: Path | None = None) -> Path:
    """The one place the current database path is decided.

    `client` and `service` each had their own copy of this expression. Partitioning
    one and not the other would split the writer from its readers, so both now call
    here. A legacy unpartitioned database beside it is never moved or removed -- it
    is the operator's history and a previous build may still be running on it.
    """

    return host_partitioned_state_dir(state_dir or common.STATE_DIR) / DATABASE_FILENAME


def socket_filename(protocol_version: int, schema_generation: int) -> str:
    """Return the service identity for one mutually compatible stats build."""

    if isinstance(protocol_version, bool) or not isinstance(protocol_version, int) or protocol_version < 1:
        raise ValueError("protocol_version must be a positive integer")
    if isinstance(schema_generation, bool) or not isinstance(schema_generation, int) or schema_generation < 1:
        raise ValueError("schema_generation must be a positive integer")
    return f"statsd.p{protocol_version}s{schema_generation}.sock"


# A stats daemon is only compatible with one writer protocol/schema pair.  Keep
# that pair in its socket identity so concurrently running worktrees cannot
# discover, fence, and replace each other's daemon.  The database has already
# been schema-scoped since v5; it deliberately remains the durable owner for
# all compatible protocol builds of this schema.
SOCKET_FILENAME = socket_filename(MIN_WRITER_PROTOCOL, SCHEMA_VERSION)
WRITER_FENCE_FILENAME = "stats-writer-compat.json"
MAX_DIRTY_INTERVALS = 32
MAX_BROWSER_FAILURE_FINGERPRINTS = 128
MAX_RING_BUCKET_BYTES = 256 * 1024

_BROWSER_PROFILE_KINDS = frozenset(
    {"api", "page_load", "finder_usable", "interaction", "operation_wait", "long_task"}
)
_BROWSER_DIAGNOSTICS_SUMMARY = "browser_observation_diagnostics"
_BROWSER_FAILURE_EVENTS = "browser_failure_diagnostic_events"
_BROWSER_FAILURE_GROUPS = "browser_failure_diagnostic_groups"
_BROWSER_FAILURE_REVISIONS = "browser_failure_diagnostic_revisions"
_BROWSER_PROFILE_EVENTS = "browser_profile_diagnostic_events"

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
        "last_vacuumed_at",
    ),
    "unavailable_spans": (
        "family", "source_id", "epoch_id", "started_at", "ended_at",
        "native_cadence_seconds", "reason", "owner_generation",
    ),
    "usage_atoms": (
        "event_id", "direction", "modality", "cache_role", "unit", "observed_at", "payload_json",
    ),
}
_RING_TABLES = frozenset(
    {"aggregate_publication", "aggregate_rings", "aggregate_ring_slots"}
)
_RING_COLUMNS = {
    "aggregate_publication": (
        "singleton", "ring_generation", "source_generation", "published_at",
    ),
    "aggregate_rings": (
        "resolution_seconds", "slot_count", "newest_bucket_start",
    ),
    "aggregate_ring_slots": (
        "resolution_seconds", "slot_index", "bucket_start", "bucket_json", "complete",
        "source_generation", "ring_generation", "published_at",
    ),
}
_RING_TRIGGER_NAMES = frozenset(
    f"{table}_reject_{operation}"
    for table in _RING_TABLES
    for operation in ("insert", "delete")
)


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
    accepted_observation_ids: tuple[str, ...] = ()
    accepted_original_timestamps: tuple[float, ...] = ()


@dataclass(frozen=True)
class RingBucketWrite:
    resolution_seconds: int
    bucket_start: int
    bucket_json: str
    complete: bool


@dataclass(frozen=True)
class RingBucketRow:
    resolution_seconds: int
    bucket_start: int
    bucket_json: str
    complete: bool
    source_generation: int
    ring_generation: int
    published_at: float


@dataclass(frozen=True)
class RingWindow:
    range_seconds: int
    resolution_seconds: int
    window_start: int
    window_end: int
    rows: tuple[RingBucketRow, ...]
    missing_bucket_starts: tuple[int, ...]
    source_generation: int
    ring_generation: int
    published_at: float


@dataclass(frozen=True)
class RingPublication:
    ring_generation: int
    source_generation: int
    published_at: float
    buckets_updated: int


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


def retention_covers_display_window() -> bool:
    """Return whether kept history covers the GUI's longest window.

    See the RETENTION_SECONDS comment: this is the one ordering that ties the two
    otherwise independent knobs together.
    """

    return RETENTION_SECONDS >= stats_resolution.MAX_RANGE_SECONDS


def _require_retention_covers_display_window() -> None:
    """Fail closed before deleting facts the GUI can still ask for.

    Pruning is the destructive half of retention. If retention were configured
    below the longest display window, the safe answer is to keep the rows and
    refuse, not to delete them and let a 24h chart render a partial window as a
    complete one.
    """

    if not retention_covers_display_window():
        raise StatsCurrentError(
            "stats retention "
            f"{RETENTION_SECONDS}s is shorter than the largest display window "
            f"{stats_resolution.MAX_RANGE_SECONDS}s; refusing to prune data the "
            "GUI can still request"
        )


def _validate_nonnegative_integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise StorageValidationError(f"{name} must be a non-negative integer")
    return value


def _ring_bucket_values(bucket: RingBucketWrite) -> tuple[int, int, int, str, int]:
    if not isinstance(bucket, RingBucketWrite):
        raise StorageValidationError("ring bucket must be a RingBucketWrite")
    resolution_seconds = _validate_nonnegative_integer(
        bucket.resolution_seconds, "resolution_seconds",
    )
    slot_count = stats_resolution.RING_CAPACITIES.get(resolution_seconds)
    if slot_count is None:
        raise StorageValidationError(
            f"resolution_seconds must be one of {tuple(stats_resolution.RING_CAPACITIES)}"
        )
    bucket_start = _validate_nonnegative_integer(bucket.bucket_start, "bucket_start")
    if bucket_start % resolution_seconds:
        raise StorageValidationError("bucket_start must align to resolution_seconds")
    if not isinstance(bucket.bucket_json, str):
        raise StorageValidationError("bucket_json must be a JSON object")
    if len(bucket.bucket_json.encode("utf-8")) > MAX_RING_BUCKET_BYTES:
        raise StorageValidationError(
            f"bucket_json exceeds {MAX_RING_BUCKET_BYTES} bytes"
        )
    try:
        payload = json.loads(bucket.bucket_json)
    except json.JSONDecodeError as error:
        raise StorageValidationError("bucket_json must be a JSON object") from error
    if not isinstance(payload, dict):
        raise StorageValidationError("bucket_json must be a JSON object")
    if not isinstance(bucket.complete, bool):
        raise StorageValidationError("complete must be a boolean")
    slot_index = (bucket_start // resolution_seconds) % slot_count
    return resolution_seconds, slot_index, bucket_start, bucket.bucket_json, int(bucket.complete)


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


def _browser_diagnostic_payload(encoded: object) -> dict[str, object] | None:
    """Decode one derived browser index input without making corruption contagious."""

    try:
        value = json.loads(str(encoded))
    except (json.JSONDecodeError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _browser_failure_dimensions(
    payload: Mapping[str, object],
) -> tuple[str, str, str, str | None] | None:
    kind = str(payload.get("kind") or "")
    if kind not in {"warning", "error", "unhandledrejection"}:
        return None
    provenance_value = payload.get("provenance")
    provenance = (
        str(provenance_value)
        if provenance_value in {"controlled_probe", "confirmed_real"}
        else "unknown"
    )
    revision_value = payload.get("code_revision")
    revision = revision_value if isinstance(revision_value, str) and revision_value else None
    return kind, str(payload.get("signature") or ""), provenance, revision


def _create_browser_diagnostics_tables(connection: sqlite3.Connection) -> None:
    """Create process-local derived indexes; original observations remain authoritative."""

    # Some SQLite builds default TEMP tables to process memory. These indexes scale with the
    # retained originals, so force spillable host-local TEMP storage and keep request memory flat.
    connection.execute("PRAGMA temp_store = FILE")
    connection.execute(
        f"CREATE TEMP TABLE IF NOT EXISTS {_BROWSER_DIAGNOSTICS_SUMMARY} ("
        "singleton INTEGER PRIMARY KEY CHECK (singleton = 1), "
        "retained_observations INTEGER NOT NULL CHECK (retained_observations >= 0), "
        "retained_failures INTEGER NOT NULL CHECK (retained_failures >= 0), "
        "retained_errors INTEGER NOT NULL CHECK (retained_errors >= 0), "
        "retained_rejections INTEGER NOT NULL CHECK (retained_rejections >= 0), "
        "confirmed_real INTEGER NOT NULL CHECK (confirmed_real >= 0), "
        "controlled_probe INTEGER NOT NULL CHECK (controlled_probe >= 0), "
        "unknown INTEGER NOT NULL CHECK (unknown >= 0), last_observed_at REAL)"
    )
    connection.execute(
        f"CREATE TEMP TABLE IF NOT EXISTS {_BROWSER_FAILURE_EVENTS} ("
        "source_id TEXT NOT NULL, event_id TEXT NOT NULL, observed_at REAL NOT NULL, "
        "kind TEXT NOT NULL, signature TEXT NOT NULL, provenance TEXT NOT NULL, "
        "code_revision TEXT, PRIMARY KEY(source_id, event_id)) WITHOUT ROWID"
    )
    connection.execute(
        f"CREATE INDEX IF NOT EXISTS browser_failure_diagnostic_events_group "
        f"ON {_BROWSER_FAILURE_EVENTS}("
        "kind, signature, provenance, observed_at DESC, event_id DESC)"
    )
    connection.execute(
        f"CREATE INDEX IF NOT EXISTS browser_failure_diagnostic_events_time "
        f"ON {_BROWSER_FAILURE_EVENTS}(observed_at)"
    )
    connection.execute(
        f"CREATE TEMP TABLE IF NOT EXISTS {_BROWSER_FAILURE_GROUPS} ("
        "kind TEXT NOT NULL, signature TEXT NOT NULL, provenance TEXT NOT NULL, "
        "failure_count INTEGER NOT NULL CHECK (failure_count > 0), "
        "first_observed_at REAL NOT NULL, last_observed_at REAL NOT NULL, "
        "latest_event_id TEXT NOT NULL, PRIMARY KEY(kind, signature, provenance)) WITHOUT ROWID"
    )
    connection.execute(
        f"CREATE INDEX IF NOT EXISTS browser_failure_diagnostic_groups_recent "
        f"ON {_BROWSER_FAILURE_GROUPS}("
        "last_observed_at DESC, signature, latest_event_id DESC, kind, provenance)"
    )
    connection.execute(
        f"CREATE TEMP TABLE IF NOT EXISTS {_BROWSER_FAILURE_REVISIONS} ("
        "kind TEXT NOT NULL, signature TEXT NOT NULL, provenance TEXT NOT NULL, "
        "code_revision TEXT NOT NULL, occurrence_count INTEGER NOT NULL "
        "CHECK (occurrence_count > 0), "
        "PRIMARY KEY(kind, signature, provenance, code_revision)) WITHOUT ROWID"
    )
    connection.execute(
        f"CREATE TEMP TABLE IF NOT EXISTS {_BROWSER_PROFILE_EVENTS} ("
        "source_id TEXT NOT NULL, event_id TEXT NOT NULL, observed_at REAL NOT NULL, "
        "payload_json TEXT NOT NULL, PRIMARY KEY(source_id, event_id)) WITHOUT ROWID"
    )
    connection.execute(
        f"CREATE INDEX IF NOT EXISTS browser_profile_diagnostic_events_recent "
        f"ON {_BROWSER_PROFILE_EVENTS}(observed_at DESC, event_id DESC)"
    )


def _rebuild_browser_failure_groups(connection: sqlite3.Connection) -> None:
    connection.execute(f"DELETE FROM {_BROWSER_FAILURE_GROUPS}")
    connection.execute(f"DELETE FROM {_BROWSER_FAILURE_REVISIONS}")
    connection.execute(
        f"INSERT INTO {_BROWSER_FAILURE_GROUPS}("
        "kind, signature, provenance, failure_count, first_observed_at, "
        "last_observed_at, latest_event_id) "
        f"SELECT kind, signature, provenance, COUNT(*), MIN(observed_at), MAX(observed_at), '' "
        f"FROM {_BROWSER_FAILURE_EVENTS} GROUP BY kind, signature, provenance"
    )
    connection.execute(
        f"UPDATE {_BROWSER_FAILURE_GROUPS} AS groups SET latest_event_id = ("
        f"SELECT events.event_id FROM {_BROWSER_FAILURE_EVENTS} AS events "
        "WHERE events.kind = groups.kind AND events.signature = groups.signature "
        "AND events.provenance = groups.provenance "
        "ORDER BY events.observed_at DESC, events.event_id DESC LIMIT 1)"
    )
    connection.execute(
        f"INSERT INTO {_BROWSER_FAILURE_REVISIONS}("
        "kind, signature, provenance, code_revision, occurrence_count) "
        f"SELECT kind, signature, provenance, code_revision, COUNT(*) "
        f"FROM {_BROWSER_FAILURE_EVENTS} WHERE code_revision IS NOT NULL "
        "GROUP BY kind, signature, provenance, code_revision"
    )


def _initialize_browser_diagnostics(connection: sqlite3.Connection) -> None:
    """Rebuild bounded request-time indexes from authoritative retained originals."""

    _create_browser_diagnostics_tables(connection)
    with _transaction(connection):
        for table in (
            _BROWSER_DIAGNOSTICS_SUMMARY,
            _BROWSER_FAILURE_EVENTS,
            _BROWSER_FAILURE_GROUPS,
            _BROWSER_FAILURE_REVISIONS,
            _BROWSER_PROFILE_EVENTS,
        ):
            connection.execute(f"DELETE FROM {table}")
        retained = failures = errors = rejections = confirmed = probes = unknown = 0
        latest: float | None = None
        failure_rows: list[tuple[object, ...]] = []
        profile_rows: list[tuple[object, ...]] = []

        def flush_rows() -> None:
            connection.executemany(
                f"INSERT INTO {_BROWSER_FAILURE_EVENTS} VALUES(?, ?, ?, ?, ?, ?, ?)",
                failure_rows,
            )
            connection.executemany(
                f"INSERT INTO {_BROWSER_PROFILE_EVENTS} VALUES(?, ?, ?, ?)",
                profile_rows,
            )
            failure_rows.clear()
            profile_rows.clear()

        rows = connection.execute(
            "SELECT source_id, event_id, observed_at, payload_json FROM observations "
            "WHERE family = 'browser'"
        )
        for source_id, event_id, observed_at, payload_json in rows:
            retained += 1
            observed = float(observed_at)
            latest = observed if latest is None else max(latest, observed)
            payload = _browser_diagnostic_payload(payload_json)
            if payload is None:
                continue
            kind = str(payload.get("kind") or "")
            if kind in _BROWSER_PROFILE_KINDS:
                profile_rows.append((source_id, event_id, observed, payload_json))
            failure = _browser_failure_dimensions(payload)
            if failure is None:
                if len(failure_rows) + len(profile_rows) >= 1_024:
                    flush_rows()
                continue
            failure_kind, signature, provenance, revision = failure
            failure_rows.append(
                (source_id, event_id, observed, failure_kind, signature, provenance, revision)
            )
            failures += 1
            errors += int(failure_kind == "error")
            rejections += int(failure_kind == "unhandledrejection")
            confirmed += int(provenance == "confirmed_real")
            probes += int(provenance == "controlled_probe")
            unknown += int(provenance == "unknown")
            if len(failure_rows) + len(profile_rows) >= 1_024:
                flush_rows()
        flush_rows()
        connection.execute(
            f"INSERT INTO {_BROWSER_DIAGNOSTICS_SUMMARY} VALUES(1, ?, ?, ?, ?, ?, ?, ?, ?)",
            (retained, failures, errors, rejections, confirmed, probes, unknown, latest),
        )
        _rebuild_browser_failure_groups(connection)


def _append_browser_diagnostics(
    connection: sqlite3.Connection,
    accepted: tuple[tuple[object, ...], ...],
) -> None:
    browser = tuple(values for values in accepted if values[1] == "browser")
    if not browser:
        return
    failure_rows: list[tuple[object, ...]] = []
    profile_rows: list[tuple[object, ...]] = []
    groups: dict[tuple[str, str, str], list[object]] = {}
    revisions: dict[tuple[str, str, str, str], int] = {}
    errors = rejections = confirmed = probes = unknown = 0
    latest = max(float(values[3]) for values in browser)
    for values in browser:
        payload = _browser_diagnostic_payload(values[6])
        if payload is None:
            continue
        kind = str(payload.get("kind") or "")
        if kind in _BROWSER_PROFILE_KINDS:
            profile_rows.append((values[2], values[0], values[3], values[6]))
        failure = _browser_failure_dimensions(payload)
        if failure is None:
            continue
        failure_kind, signature, provenance, revision = failure
        event_id, observed = str(values[0]), float(values[3])
        failure_rows.append(
            (values[2], event_id, observed, failure_kind, signature, provenance, revision)
        )
        errors += int(failure_kind == "error")
        rejections += int(failure_kind == "unhandledrejection")
        confirmed += int(provenance == "confirmed_real")
        probes += int(provenance == "controlled_probe")
        unknown += int(provenance == "unknown")
        key = (failure_kind, signature, provenance)
        item = groups.setdefault(key, [0, observed, observed, event_id])
        item[0] = int(item[0]) + 1
        item[1] = min(float(item[1]), observed)
        if observed > float(item[2]) or (observed == float(item[2]) and event_id > str(item[3])):
            item[2], item[3] = observed, event_id
        if revision is not None:
            revision_key = (*key, revision)
            revisions[revision_key] = revisions.get(revision_key, 0) + 1
    connection.execute(
        f"UPDATE {_BROWSER_DIAGNOSTICS_SUMMARY} SET "
        "retained_observations = retained_observations + ?, "
        "retained_failures = retained_failures + ?, retained_errors = retained_errors + ?, "
        "retained_rejections = retained_rejections + ?, confirmed_real = confirmed_real + ?, "
        "controlled_probe = controlled_probe + ?, unknown = unknown + ?, "
        "last_observed_at = CASE WHEN last_observed_at IS NULL OR last_observed_at < ? "
        "THEN ? ELSE last_observed_at END WHERE singleton = 1",
        (
            len(browser), len(failure_rows), errors, rejections, confirmed, probes, unknown,
            latest, latest,
        ),
    )
    connection.executemany(
        f"INSERT INTO {_BROWSER_FAILURE_EVENTS} VALUES(?, ?, ?, ?, ?, ?, ?)",
        failure_rows,
    )
    connection.executemany(
        f"INSERT INTO {_BROWSER_PROFILE_EVENTS} VALUES(?, ?, ?, ?)",
        profile_rows,
    )
    connection.executemany(
        f"INSERT INTO {_BROWSER_FAILURE_GROUPS}("
        "kind, signature, provenance, failure_count, first_observed_at, "
        "last_observed_at, latest_event_id) VALUES(?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(kind, signature, provenance) DO UPDATE SET "
        "failure_count = failure_count + excluded.failure_count, "
        "first_observed_at = MIN(first_observed_at, excluded.first_observed_at), "
        "latest_event_id = CASE WHEN excluded.last_observed_at > last_observed_at "
        "OR (excluded.last_observed_at = last_observed_at "
        "AND excluded.latest_event_id > latest_event_id) THEN excluded.latest_event_id "
        "ELSE latest_event_id END, "
        "last_observed_at = MAX(last_observed_at, excluded.last_observed_at)",
        ((*key, *item) for key, item in groups.items()),
    )
    connection.executemany(
        f"INSERT INTO {_BROWSER_FAILURE_REVISIONS}("
        "kind, signature, provenance, code_revision, occurrence_count) "
        "VALUES(?, ?, ?, ?, ?) "
        "ON CONFLICT(kind, signature, provenance, code_revision) DO UPDATE SET "
        "occurrence_count = occurrence_count + excluded.occurrence_count",
        ((*key, count) for key, count in revisions.items()),
    )


def _prune_browser_diagnostics(connection: sqlite3.Connection, cutoff: float) -> None:
    removed_observations = int(connection.execute(
        "SELECT COUNT(*) FROM observations WHERE family = 'browser' AND observed_at < ?",
        (cutoff,),
    ).fetchone()[0])
    if not removed_observations:
        return
    removed_rows = connection.execute(
        f"SELECT kind, signature, provenance, code_revision, COUNT(*) "
        f"FROM {_BROWSER_FAILURE_EVENTS} WHERE observed_at < ? "
        "GROUP BY kind, signature, provenance, code_revision",
        (cutoff,),
    ).fetchall()
    removed_groups: dict[tuple[str, str, str], int] = {}
    failures = errors = rejections = confirmed = probes = unknown = 0
    for kind, signature, provenance, _revision, count_value in removed_rows:
        count = int(count_value)
        key = (str(kind), str(signature), str(provenance))
        removed_groups[key] = removed_groups.get(key, 0) + count
        failures += count
        errors += count * int(kind == "error")
        rejections += count * int(kind == "unhandledrejection")
        confirmed += count * int(provenance == "confirmed_real")
        probes += count * int(provenance == "controlled_probe")
        unknown += count * int(provenance == "unknown")
    connection.execute(
        f"DELETE FROM {_BROWSER_FAILURE_EVENTS} WHERE observed_at < ?", (cutoff,),
    )
    connection.execute(
        f"DELETE FROM {_BROWSER_PROFILE_EVENTS} WHERE observed_at < ?", (cutoff,),
    )
    for key, count in removed_groups.items():
        current = connection.execute(
            f"SELECT failure_count FROM {_BROWSER_FAILURE_GROUPS} "
            "WHERE kind = ? AND signature = ? AND provenance = ?",
            key,
        ).fetchone()
        if current is None or int(current[0]) < count:
            raise SchemaMismatchError("browser failure diagnostic group count is inconsistent")
        if int(current[0]) == count:
            connection.execute(
                f"DELETE FROM {_BROWSER_FAILURE_GROUPS} "
                "WHERE kind = ? AND signature = ? AND provenance = ?",
                key,
            )
            connection.execute(
                f"DELETE FROM {_BROWSER_FAILURE_REVISIONS} "
                "WHERE kind = ? AND signature = ? AND provenance = ?",
                key,
            )
            continue
        first = connection.execute(
            f"SELECT MIN(observed_at) FROM {_BROWSER_FAILURE_EVENTS} "
            "WHERE kind = ? AND signature = ? AND provenance = ?",
            key,
        ).fetchone()[0]
        connection.execute(
            f"UPDATE {_BROWSER_FAILURE_GROUPS} SET failure_count = failure_count - ?, "
            "first_observed_at = ? WHERE kind = ? AND signature = ? AND provenance = ?",
            (count, first, *key),
        )
    for kind, signature, provenance, revision, count_value in removed_rows:
        if revision is None:
            continue
        key = (str(kind), str(signature), str(provenance), str(revision))
        current = connection.execute(
            f"SELECT occurrence_count FROM {_BROWSER_FAILURE_REVISIONS} "
            "WHERE kind = ? AND signature = ? AND provenance = ? AND code_revision = ?",
            key,
        ).fetchone()
        if current is None:
            continue
        count = int(count_value)
        if int(current[0]) <= count:
            connection.execute(
                f"DELETE FROM {_BROWSER_FAILURE_REVISIONS} "
                "WHERE kind = ? AND signature = ? AND provenance = ? AND code_revision = ?",
                key,
            )
        else:
            connection.execute(
                f"UPDATE {_BROWSER_FAILURE_REVISIONS} "
                "SET occurrence_count = occurrence_count - ? "
                "WHERE kind = ? AND signature = ? AND provenance = ? AND code_revision = ?",
                (count, *key),
            )
    remaining = connection.execute(
        f"SELECT retained_observations - ? FROM {_BROWSER_DIAGNOSTICS_SUMMARY} WHERE singleton = 1",
        (removed_observations,),
    ).fetchone()
    if remaining is None or int(remaining[0]) < 0:
        raise SchemaMismatchError("browser observation diagnostic count is inconsistent")
    connection.execute(
        f"UPDATE {_BROWSER_DIAGNOSTICS_SUMMARY} SET "
        "retained_observations = retained_observations - ?, "
        "retained_failures = retained_failures - ?, retained_errors = retained_errors - ?, "
        "retained_rejections = retained_rejections - ?, confirmed_real = confirmed_real - ?, "
        "controlled_probe = controlled_probe - ?, unknown = unknown - ?, "
        "last_observed_at = CASE WHEN retained_observations = ? THEN NULL ELSE last_observed_at END "
        "WHERE singleton = 1",
        (
            removed_observations, failures, errors, rejections,
            confirmed, probes, unknown, removed_observations,
        ),
    )


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
    return tuple(merged)


def _time_clauses(
    intervals: tuple[tuple[float, float], ...] | None,
) -> tuple[tuple[str, tuple[float, ...]], ...]:
    if intervals is None:
        return (("", ()),)
    if not intervals:
        return ((" WHERE 0", ()),)
    if len(intervals) > MAX_DIRTY_INTERVALS * 2:
        # Repeating many small indexed reads is slower than one bounded range
        # read on the host-backed database; the caller sweeps this envelope
        # against the exact intervals before decoding any JSON payload.
        return ((
            " WHERE observed_at >= ? AND observed_at < ?",
            (intervals[0][0], intervals[-1][1]),
        ),)
    return tuple(
        (
            " WHERE " + " OR ".join(
                "(observed_at >= ? AND observed_at < ?)" for _interval in batch
            ),
            tuple(value for interval in batch for value in interval),
        )
        for offset in range(0, len(intervals), MAX_DIRTY_INTERVALS)
        for batch in (intervals[offset:offset + MAX_DIRTY_INTERVALS],)
    )


def _rows_in_dirty_intervals(
    rows: tuple[tuple[object, ...], ...],
    intervals: tuple[tuple[float, float], ...] | None,
    timestamp_index: int,
) -> tuple[tuple[object, ...], ...]:
    if intervals is None or len(intervals) <= MAX_DIRTY_INTERVALS * 2:
        return rows
    selected = []
    interval_index = 0
    for row in rows:
        observed_at = float(row[timestamp_index])
        while (
            interval_index < len(intervals)
            and intervals[interval_index][1] <= observed_at
        ):
            interval_index += 1
        if interval_index >= len(intervals):
            break
        if intervals[interval_index][0] <= observed_at:
            selected.append(row)
    return tuple(selected)


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


def _aggregate_tables(connection: sqlite3.Connection) -> frozenset[str]:
    return frozenset(
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'aggregate_%'"
        )
    )


def _validate_ring_schema(connection: sqlite3.Connection) -> None:
    tables = _aggregate_tables(connection)
    if tables != _RING_TABLES:
        raise SchemaMismatchError(
            f"expected aggregate tables {sorted(_RING_TABLES)}, found {sorted(tables)}"
        )
    columns = {
        table: tuple(str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})"))
        for table in tables
    }
    if columns != _RING_COLUMNS:
        raise SchemaMismatchError("aggregate ring columns do not match the exact schema")
    publication_rows = connection.execute(
        "SELECT singleton, ring_generation, source_generation, published_at "
        "FROM aggregate_publication"
    ).fetchall()
    if len(publication_rows) != 1 or tuple(publication_rows[0])[:1] != (1,):
        raise SchemaMismatchError("aggregate publication must contain its one fixed row")
    ring_rows = connection.execute(
        "SELECT resolution_seconds, slot_count, newest_bucket_start "
        "FROM aggregate_rings ORDER BY resolution_seconds"
    ).fetchall()
    expected_rings = tuple(
        (resolution_seconds, slot_count)
        for resolution_seconds, slot_count in stats_resolution.RING_CAPACITIES.items()
    )
    if tuple((int(row[0]), int(row[1])) for row in ring_rows) != expected_rings:
        raise SchemaMismatchError("aggregate rings do not match the exact capacities")
    for row in ring_rows:
        if row[2] is not None and int(row[2]) % int(row[0]):
            raise SchemaMismatchError("aggregate ring head is not resolution-aligned")
    slot_counts = tuple(
        (int(row[0]), int(row[1]))
        for row in connection.execute(
            "SELECT resolution_seconds, count(*) FROM aggregate_ring_slots "
            "GROUP BY resolution_seconds ORDER BY resolution_seconds"
        )
    )
    if slot_counts != expected_rings:
        raise SchemaMismatchError("aggregate ring slot counts do not match the exact capacities")
    for resolution_seconds, slot_count in expected_rings:
        indexes = tuple(
            int(row[0])
            for row in connection.execute(
                "SELECT slot_index FROM aggregate_ring_slots "
                "WHERE resolution_seconds = ? ORDER BY slot_index",
                (resolution_seconds,),
            )
        )
        if indexes != tuple(range(slot_count)):
            raise SchemaMismatchError(
                f"aggregate ring {resolution_seconds}s slots are not exact and contiguous"
            )
    trigger_names = frozenset(
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger' "
            "AND tbl_name LIKE 'aggregate_%'"
        )
    )
    if trigger_names != _RING_TRIGGER_NAMES:
        raise SchemaMismatchError("aggregate ring fixed-row triggers do not match the exact schema")


def _initialize_ring_schema(connection: sqlite3.Connection) -> None:
    tables = _aggregate_tables(connection)
    if tables:
        _validate_ring_schema(connection)
        return
    with _transaction(connection):
        connection.execute(
            "CREATE TABLE aggregate_publication ("
            "singleton INTEGER PRIMARY KEY CHECK (singleton = 1), "
            "ring_generation INTEGER NOT NULL CHECK (ring_generation >= 0), "
            "source_generation INTEGER NOT NULL CHECK (source_generation >= 0), "
            "published_at REAL NOT NULL CHECK (published_at >= 0))"
        )
        connection.execute(
            "CREATE TABLE aggregate_rings ("
            "resolution_seconds INTEGER PRIMARY KEY, "
            "slot_count INTEGER NOT NULL CHECK (slot_count > 0), "
            "newest_bucket_start INTEGER, "
            "CHECK (resolution_seconds > 0), "
            "CHECK (newest_bucket_start IS NULL OR newest_bucket_start >= 0), "
            "CHECK (newest_bucket_start IS NULL OR "
            "newest_bucket_start % resolution_seconds = 0)) WITHOUT ROWID"
        )
        connection.execute(
            "CREATE TABLE aggregate_ring_slots ("
            "resolution_seconds INTEGER NOT NULL, "
            "slot_index INTEGER NOT NULL CHECK (slot_index >= 0), "
            "bucket_start INTEGER, bucket_json TEXT, "
            "complete INTEGER NOT NULL DEFAULT 0 CHECK (complete IN (0, 1)), "
            "source_generation INTEGER NOT NULL DEFAULT 0 CHECK (source_generation >= 0), "
            "ring_generation INTEGER NOT NULL DEFAULT 0 CHECK (ring_generation >= 0), "
            "published_at REAL NOT NULL DEFAULT 0 CHECK (published_at >= 0), "
            "PRIMARY KEY (resolution_seconds, slot_index), "
            "FOREIGN KEY (resolution_seconds) REFERENCES aggregate_rings(resolution_seconds), "
            "CHECK (bucket_start IS NULL OR bucket_start >= 0), "
            "CHECK (bucket_start IS NULL OR bucket_start % resolution_seconds = 0), "
            "CHECK ((bucket_start IS NULL AND bucket_json IS NULL AND complete = 0 "
            "AND source_generation = 0 AND ring_generation = 0 AND published_at = 0) "
            "OR (bucket_start IS NOT NULL AND bucket_json IS NOT NULL))) WITHOUT ROWID"
        )
        connection.execute(
            "INSERT INTO aggregate_publication("
            "singleton, ring_generation, source_generation, published_at) VALUES(1, 0, 0, 0)"
        )
        connection.executemany(
            "INSERT INTO aggregate_rings("
            "resolution_seconds, slot_count, newest_bucket_start) VALUES(?, ?, NULL)",
            stats_resolution.RING_CAPACITIES.items(),
        )
        connection.executemany(
            "INSERT INTO aggregate_ring_slots(resolution_seconds, slot_index) VALUES(?, ?)",
            (
                (resolution_seconds, slot_index)
                for resolution_seconds, slot_count in stats_resolution.RING_CAPACITIES.items()
                for slot_index in range(slot_count)
            ),
        )
        for table in sorted(_RING_TABLES):
            for operation in ("insert", "delete"):
                connection.execute(
                    f"CREATE TRIGGER {table}_reject_{operation} "
                    f"BEFORE {operation.upper()} ON {table} "
                    "BEGIN SELECT RAISE(ABORT, 'aggregate ring rows are fixed'); END"
                )
    _validate_ring_schema(connection)


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
        # Before the file is opened or created, not after: WAL is unsupported on a
        # network filesystem, and a shared home puts two hosts on one inode.
        preflight_mutable_roots(wal_databases=[database_path])
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
            connection.execute(f"PRAGMA wal_autocheckpoint = {WAL_AUTOCHECKPOINT_PAGES}")
            connection.execute(f"PRAGMA journal_size_limit = {WAL_ALLOCATION_CEILING_BYTES}")
            _initialize_browser_diagnostics(connection)
            # A clean writer takeover must not inherit the largest WAL allocation
            # a prior large transaction left behind. The service singleton lock
            # makes this the safe startup boundary before worker readers exist.
            _truncate_wal(connection)
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
            connection.execute("PRAGMA busy_timeout = 5000")
            header = _read_header(connection)
            _check_writer(header, protocol, build)
            cls._validate_schema_shape(connection)
            _initialize_browser_diagnostics(connection)
            connection.execute("PRAGMA query_only = ON")
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
            value = read_json_file(fence_path, None, exceptions=(FileNotFoundError,))
        except (OSError, json.JSONDecodeError) as error:
            raise SchemaMismatchError("stats writer fence cannot be read") from error
        if value is None:
            return
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
                "source_generation INTEGER NOT NULL CHECK (source_generation >= 0), "
                "last_vacuumed_at REAL NOT NULL DEFAULT 0 CHECK (last_vacuumed_at >= 0))"
            )
            connection.execute(
                "INSERT INTO schema_meta(singleton, minimum_writer_protocol, minimum_writer_build, "
                "source_generation, last_vacuumed_at) VALUES(?, ?, ?, ?, ?)",
                (1, MIN_WRITER_PROTOCOL, MIN_WRITER_BUILD, 0, 0.0),
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
        # Landing 2 keeps schema v7 active and makes the ring kernel explicit. Reopening an opted-in
        # test or migration candidate may accept the whole exact extension, never a partial shape.
        aggregate_tables = tables & _RING_TABLES
        if tables - _RING_TABLES != _TABLES or aggregate_tables not in (set(), set(_RING_TABLES)):
            raise SchemaMismatchError(
                f"expected current tables {sorted(_TABLES)}, found {sorted(tables)}"
            )
        columns = {
            table: tuple(str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})"))
            for table in _TABLES
        }
        if columns != _COLUMNS:
            raise SchemaMismatchError("current stats table columns do not match the exact schema")
        if aggregate_tables:
            _validate_ring_schema(connection)

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

    def initialize_ring_storage(self) -> None:
        """Create the inert fixed-slot kernel without enabling a production caller."""

        if self.read_only:
            raise StatsCurrentError("stats store reader cannot initialize ring storage")
        _initialize_ring_schema(self._connection())

    def _require_ring_storage(self) -> sqlite3.Connection:
        connection = self._connection()
        tables = _aggregate_tables(connection)
        if tables != _RING_TABLES:
            raise StatsCurrentError("aggregate ring storage is not initialized")
        return connection

    def publish_ring_buckets(
        self,
        *,
        buckets: Iterable[RingBucketWrite],
        source_generation: int,
        published_at: float,
    ) -> RingPublication:
        """Replace exact addressed slots in one update-only transaction."""

        if self.read_only:
            raise StatsCurrentError("stats store reader cannot publish ring rows")
        prepared = tuple(_ring_bucket_values(bucket) for bucket in buckets)
        if not prepared:
            raise StorageValidationError("ring publication must contain at least one bucket")
        source = _validate_nonnegative_integer(source_generation, "source_generation")
        published = _validate_timestamp(published_at, "published_at")
        connection = self._require_ring_storage()
        with _transaction(connection):
            previous = connection.execute(
                "SELECT ring_generation, source_generation, published_at "
                "FROM aggregate_publication WHERE singleton = 1"
            ).fetchone()
            if previous is None:
                raise SchemaMismatchError("aggregate publication row is missing")
            if source < int(previous[1]):
                raise StorageValidationError("source_generation cannot move backward")
            if published < float(previous[2]):
                raise StorageValidationError("published_at cannot move backward")
            ring_generation = int(previous[0]) + 1
            newest_by_resolution: dict[int, int] = {}
            for resolution_seconds, slot_index, bucket_start, bucket_json, complete in prepared:
                changed = connection.execute(
                    "UPDATE aggregate_ring_slots SET bucket_start = ?, bucket_json = ?, "
                    "complete = ?, source_generation = ?, ring_generation = ?, published_at = ? "
                    "WHERE resolution_seconds = ? AND slot_index = ?",
                    (
                        bucket_start, bucket_json, complete, source, ring_generation, published,
                        resolution_seconds, slot_index,
                    ),
                ).rowcount
                if changed != 1:
                    raise SchemaMismatchError("aggregate ring slot address is missing")
                newest_by_resolution[resolution_seconds] = max(
                    bucket_start,
                    newest_by_resolution.get(resolution_seconds, bucket_start),
                )
            for resolution_seconds, newest_bucket_start in newest_by_resolution.items():
                changed = connection.execute(
                    "UPDATE aggregate_rings SET newest_bucket_start = CASE "
                    "WHEN newest_bucket_start IS NULL OR newest_bucket_start < ? THEN ? "
                    "ELSE newest_bucket_start END WHERE resolution_seconds = ?",
                    (newest_bucket_start, newest_bucket_start, resolution_seconds),
                ).rowcount
                if changed != 1:
                    raise SchemaMismatchError("aggregate ring metadata row is missing")
            changed = connection.execute(
                "UPDATE aggregate_publication SET ring_generation = ?, source_generation = ?, "
                "published_at = ? WHERE singleton = 1",
                (ring_generation, source, published),
            ).rowcount
            if changed != 1:
                raise SchemaMismatchError("aggregate publication row is missing")
        return RingPublication(ring_generation, source, published, len(prepared))

    def read_ring_window(
        self,
        *,
        range_seconds: int,
        resolution_seconds: int,
        window_end: int,
    ) -> RingWindow:
        """Read exact timestamps from one resolution ring in one SQLite snapshot."""

        range_value = _validate_nonnegative_integer(range_seconds, "range_seconds")
        resolution_value = _validate_nonnegative_integer(
            resolution_seconds, "resolution_seconds",
        )
        end = _validate_nonnegative_integer(window_end, "window_end")
        if not stats_resolution.is_supported(range_value, resolution_value):
            raise StorageValidationError(
                f"unsupported ring window {range_value}s/{resolution_value}s"
            )
        if end < range_value or end % resolution_value:
            raise StorageValidationError(
                "window_end must be resolution-aligned and cover the requested range"
            )
        connection = self._require_ring_storage()
        window_start = end - range_value
        expected_starts = tuple(range(window_start, end, resolution_value))
        slot_count = stats_resolution.RING_CAPACITIES[resolution_value]
        with _transaction(connection):
            publication = connection.execute(
                "SELECT ring_generation, source_generation, published_at "
                "FROM aggregate_publication WHERE singleton = 1"
            ).fetchone()
            if publication is None:
                raise SchemaMismatchError("aggregate publication row is missing")
            slot_rows = {
                int(row[0]): row
                for row in connection.execute(
                    "SELECT slot_index, bucket_start, bucket_json, complete, "
                    "source_generation, ring_generation, published_at "
                    "FROM aggregate_ring_slots WHERE resolution_seconds = ?",
                    (resolution_value,),
                )
            }
            rows: list[RingBucketRow] = []
            missing: list[int] = []
            for bucket_start in expected_starts:
                slot_index = (bucket_start // resolution_value) % slot_count
                candidate = slot_rows.get(slot_index)
                if candidate is None or candidate[1] is None or int(candidate[1]) != bucket_start:
                    missing.append(bucket_start)
                    continue
                rows.append(
                    RingBucketRow(
                        resolution_value,
                        bucket_start,
                        str(candidate[2]),
                        bool(candidate[3]),
                        int(candidate[4]),
                        int(candidate[5]),
                        float(candidate[6]),
                    )
                )
        return RingWindow(
            range_value,
            resolution_value,
            window_start,
            end,
            tuple(rows),
            tuple(missing),
            int(publication[1]),
            int(publication[0]),
            float(publication[2]),
        )

    def last_vacuumed_at(self) -> float:
        """Return the persisted completion time for the last successful VACUUM."""
        row = self._connection().execute(
            "SELECT last_vacuumed_at FROM schema_meta WHERE singleton = 1"
        ).fetchone()
        if row is None:
            raise SchemaMismatchError("current stats vacuum metadata is missing")
        return float(row[0])

    def vacuum(self, completed_at: float) -> float:
        """Compact the writer database and persist completion only after success.

        SQLite forbids VACUUM inside a transaction.  A crash or SQLite failure before the following
        metadata transaction intentionally leaves the prior marker intact so a later idle cycle may
        retry; callers must serialize this with their sole writer lock.
        """
        if self.read_only:
            raise StatsCurrentError("stats store reader cannot vacuum the database")
        timestamp = _validate_timestamp(completed_at, "completed_at")
        connection = self._connection()
        connection.execute("VACUUM")
        connection.execute("PRAGMA optimize")
        # A passive/autocheckpoint recycles logical frames but retains the WAL's
        # largest allocation. VACUUM can therefore leave a database-sized file
        # behind even though every frame was checkpointed. Truncate before the
        # completion marker so a blocked checkpoint keeps the rewrite due.
        _truncate_wal(connection)
        with _transaction(connection):
            connection.execute(
                "UPDATE schema_meta SET last_vacuumed_at = ? WHERE singleton = 1",
                (timestamp,),
            )
        # The marker transaction creates a few new frames after the first
        # truncate. Remove those too so a successful rewrite has one exact
        # physical postcondition rather than a history-dependent allocation.
        _truncate_wal(connection)
        return timestamp

    def _apply_observations(
        self, connection: sqlite3.Connection, prepared: tuple[tuple[object, ...], ...],
    ) -> tuple[int, tuple[tuple[object, ...], ...]]:
        accepted = 0
        accepted_values: list[tuple[object, ...]] = []
        for values in prepared:
            previous = connection.execute(
                "SELECT observed_at, epoch_id, owner_generation, payload_json FROM observations "
                "WHERE event_id = ? AND family = ? AND source_id = ?", values[:3],
            ).fetchone()
            if previous is None:
                connection.execute(
                    "INSERT INTO observations(event_id, family, source_id, observed_at, epoch_id, "
                    "owner_generation, payload_json) VALUES(?, ?, ?, ?, ?, ?, ?)", values,
                )
                accepted += 1
                accepted_values.append(values)
            elif tuple(previous) != values[3:]:
                raise StorageValidationError("observation event identity conflicts with stored data")
        return accepted, tuple(accepted_values)

    def _apply_coverage_epochs(
        self, connection: sqlite3.Connection, prepared: tuple[tuple[object, ...], ...],
    ) -> int:
        changed = 0
        for values in prepared:
            key = values[:3]
            previous = connection.execute(
                "SELECT started_at, ended_at, native_cadence_seconds, owner_generation "
                "FROM coverage_epochs WHERE family = ? AND source_id = ? AND epoch_id = ?", key,
            ).fetchone()
            current = values[3:]
            conflict = connection.execute(
                "SELECT 1 FROM unavailable_spans WHERE family = ? AND source_id = ? "
                "AND ended_at > ? AND (? IS NULL OR started_at < ?) LIMIT 1",
                (values[0], values[1], current[0], current[1], current[1]),
            ).fetchone()
            if conflict is not None:
                raise StorageValidationError("coverage epoch overlaps an unavailable span")
            if previous is None:
                connection.execute(
                    "INSERT INTO coverage_epochs(family, source_id, epoch_id, started_at, ended_at, "
                    "native_cadence_seconds, owner_generation) VALUES(?, ?, ?, ?, ?, ?, ?)", values,
                )
                changed += 1
            elif tuple(previous) != current:
                if previous[0] != current[0] or previous[2] != current[2]:
                    raise StorageValidationError("coverage epoch start and cadence are immutable")
                if previous[1] is not None and (current[1] is None or current[1] < previous[1]):
                    raise StorageValidationError("coverage epoch end cannot move backward")
                if current[3] < previous[3]:
                    raise StorageValidationError("coverage owner_generation cannot move backward")
                connection.execute(
                    "UPDATE coverage_epochs SET ended_at = ?, owner_generation = ? "
                    "WHERE family = ? AND source_id = ? AND epoch_id = ?", (current[1], current[3], *key),
                )
                changed += 1
        return changed

    def _apply_usage_atoms(
        self, connection: sqlite3.Connection, prepared: tuple[tuple[object, ...], ...],
    ) -> tuple[int, int, tuple[tuple[object, ...], ...]]:
        accepted = conflicts = 0
        accepted_values: list[tuple[object, ...]] = []
        for values in prepared:
            previous = connection.execute(
                "SELECT observed_at, payload_json FROM usage_atoms WHERE event_id = ? "
                "AND direction = ? AND modality = ? AND cache_role = ? AND unit = ?", values[:5],
            ).fetchone()
            if previous is None:
                connection.execute(
                    "INSERT INTO usage_atoms(event_id, direction, modality, cache_role, unit, observed_at, "
                    "payload_json) VALUES(?, ?, ?, ?, ?, ?, ?)", values,
                )
                accepted += 1
                accepted_values.append(values)
            elif tuple(previous) != values[5:]:
                repaired = _usage_unknown_model_repair(tuple(previous), values[5:])
                compatible, agent_changed = _usage_compatible_metadata_change(tuple(previous), values[5:])
                if repaired is not None:
                    payload_json, repair_agent_changed = repaired
                    connection.execute(
                        "UPDATE usage_atoms SET payload_json = ? WHERE event_id = ? AND direction = ? "
                        "AND modality = ? AND cache_role = ? AND unit = ?", (payload_json, *values[:5]),
                    )
                    accepted += 1
                    accepted_values.append(values)
                    conflicts += int(repair_agent_changed)
                elif compatible:
                    conflicts += int(agent_changed)
                else:
                    raise UsageAtomIdentityConflict(
                        event_id=str(values[0]), identity_hash=_usage_conflict_hash(tuple(values[:5])),
                        first_payload_hash=_usage_conflict_hash(tuple(previous)),
                        attempted_payload_hash=_usage_conflict_hash(tuple(values[5:])),
                    )
        return accepted, conflicts, tuple(accepted_values)

    def _apply_usage_tombstones(
        self, connection: sqlite3.Connection, prepared: tuple[tuple[object, ...], ...],
    ) -> tuple[int, int, tuple[tuple[object, ...], ...]]:
        accepted = duplicate = 0
        accepted_values: list[tuple[object, ...]] = []
        for values in prepared:
            key = values[:5]
            previous = connection.execute(
                "SELECT observed_at, payload_json FROM usage_atoms WHERE event_id = ? "
                "AND direction = ? AND modality = ? AND cache_role = ? AND unit = ?", key,
            ).fetchone()
            if previous is None:
                duplicate += 1
                continue
            payload = _decode_json_object(previous[1], "usage atom payload")
            expected = (values[5], values[6], values[7], values[8], values[9], "codex")
            actual = (float(previous[0]), payload.get("quantity"), payload.get("provider"), payload.get("model"), payload.get("thread_id"), payload.get("execution_source"))
            if actual != expected:
                raise StorageValidationError("usage tombstone conflicts with stored data")
            connection.execute(
                "DELETE FROM usage_atoms WHERE event_id = ? AND direction = ? AND modality = ? "
                "AND cache_role = ? AND unit = ?", key,
            )
            accepted += 1
            accepted_values.append(values)
        return accepted, duplicate, tuple(accepted_values)

    def _apply_unavailable_spans(
        self, connection: sqlite3.Connection, prepared: tuple[tuple[object, ...], ...],
    ) -> int:
        accepted = 0
        for values in prepared:
            previous = connection.execute(
                "SELECT native_cadence_seconds, reason, owner_generation FROM unavailable_spans "
                "WHERE family = ? AND source_id = ? AND epoch_id = ? AND started_at = ? AND ended_at = ?", values[:5],
            ).fetchone()
            if previous is None:
                coverage_conflict = connection.execute(
                    "SELECT 1 FROM coverage_epochs WHERE family = ? AND source_id = ? "
                    "AND (ended_at IS NULL OR ended_at > ?) AND started_at < ? LIMIT 1", (values[0], values[1], values[3], values[4]),
                ).fetchone()
                if coverage_conflict is not None:
                    raise StorageValidationError("unavailable span overlaps a coverage epoch")
                unavailable_conflict = connection.execute(
                    "SELECT 1 FROM unavailable_spans WHERE family = ? AND source_id = ? "
                    "AND ended_at > ? AND started_at < ? LIMIT 1", (values[0], values[1], values[3], values[4]),
                ).fetchone()
                if unavailable_conflict is not None:
                    raise StorageValidationError("unavailable spans overlap")
                connection.execute(
                    "INSERT INTO unavailable_spans(family, source_id, epoch_id, started_at, ended_at, "
                    "native_cadence_seconds, reason, owner_generation) VALUES(?, ?, ?, ?, ?, ?, ?, ?)", values,
                )
                accepted += 1
            elif tuple(previous) != values[5:]:
                raise StorageValidationError("unavailable span identity conflicts with stored data")
        return accepted

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
            observations_accepted, accepted_observations = self._apply_observations(
                connection, prepared_observations,
            )
            _append_browser_diagnostics(connection, accepted_observations)
            coverage_changed = self._apply_coverage_epochs(connection, prepared_coverage)
            usage_accepted, usage_attribution_conflicts, accepted_usage = self._apply_usage_atoms(
                connection, prepared_usage,
            )
            tombstones_accepted, tombstones_duplicate, accepted_tombstones = self._apply_usage_tombstones(
                connection, prepared_tombstones,
            )
            unavailable_accepted = self._apply_unavailable_spans(
                connection, prepared_unavailable,
            )
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
            tuple(str(values[0]) for values in accepted_observations),
            tuple(
                float(values[index])
                for accepted, index in (
                    (accepted_observations, 3),
                    (accepted_usage, 5),
                    (accepted_tombstones, 5),
                )
                for values in accepted
            ),
        )

    def append_observation(self, observation: Observation) -> bool:
        return self.append_batch(observations=(observation,)).observations_accepted == 1

    def append_coverage_epoch(self, coverage: CoverageEpoch) -> bool:
        return self.append_batch(coverage_epochs=(coverage,)).coverage_changed == 1

    def latest_coverage_epoch(
        self,
        family: str,
        source_id: str,
        owner_generation: int,
        native_cadence_seconds: float,
    ) -> CoverageEpoch | None:
        """Read the latest exact source lifecycle without scanning retained coverage."""

        family_value = _validate_text(family, "family")
        source_value = _validate_text(source_id, "source_id")
        owner_value = _validate_nonnegative_integer(owner_generation, "owner_generation")
        cadence_value = _validate_timestamp(
            native_cadence_seconds,
            "native_cadence_seconds",
        )
        row = self._connection().execute(
            "SELECT epoch_id, started_at, ended_at FROM coverage_epochs "
            "WHERE family = ? AND source_id = ? AND owner_generation = ? "
            "AND native_cadence_seconds = ? ORDER BY started_at DESC, epoch_id DESC LIMIT 1",
            (family_value, source_value, owner_value, cadence_value),
        ).fetchone()
        if row is None:
            return None
        return CoverageEpoch(
            family_value,
            source_value,
            str(row[0]),
            float(row[1]),
            None if row[2] is None else float(row[2]),
            cadence_value,
            owner_value,
        )

    def inline_coverage_source_ids(
        self,
        family: str,
        owner_generation: int,
    ) -> tuple[str, ...]:
        """Read the canonical inline source roster for one exact owner."""

        family_value = _validate_text(family, "family")
        owner_value = _validate_nonnegative_integer(owner_generation, "owner_generation")
        epoch_prefix = f"inline:{owner_value}:{family_value}:"
        rows = self._connection().execute(
            "SELECT DISTINCT source_id FROM coverage_epochs "
            "WHERE family = ? AND owner_generation = ? AND substr(epoch_id, 1, ?) = ? "
            "ORDER BY source_id",
            (family_value, owner_value, len(epoch_prefix), epoch_prefix),
        ).fetchall()
        return tuple(str(row[0]) for row in rows)

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
        include_coverage: bool = True,
    ) -> StoreSnapshot:
        """Read all coverage plus either full or dirty-window original facts."""

        with self.pinned_snapshot(
            dirty_intervals=dirty_intervals,
            include_coverage=include_coverage,
        ) as read:
            return read()

    def recent_browser_profiles(self, limit: int = 128) -> tuple[dict[str, object], ...]:
        """Read bounded durable request, page, and perceptual profiles newest first."""

        bounded_limit = _validate_nonnegative_integer(limit, "browser profile limit")
        if not 1 <= bounded_limit <= 128:
            raise StorageValidationError("browser profile limit must be from 1 through 128")
        rows = self._connection().execute(
            f"SELECT observed_at, payload_json FROM {_BROWSER_PROFILE_EVENTS} "
            "ORDER BY observed_at DESC, event_id DESC LIMIT ?",
            (bounded_limit,),
        ).fetchall()
        return tuple(
            {"observed_at": float(row[0]), **_decode_json_object(row[1], "browser profile payload")}
            for row in rows
        )

    def browser_observation_status(self, now: float) -> dict[str, object]:
        """Summarize retained browser facts without creating a second receipt ledger."""

        connection = self._connection()
        summary = connection.execute(
            f"SELECT retained_observations, retained_failures, retained_errors, "
            "retained_rejections, confirmed_real, controlled_probe, unknown, last_observed_at "
            f"FROM {_BROWSER_DIAGNOSTICS_SUMMARY} WHERE singleton = 1"
        ).fetchone()
        if summary is None:
            raise SchemaMismatchError("browser observation diagnostics summary is missing")
        failure_groups = connection.execute(
            f"SELECT kind, signature, provenance, failure_count, first_observed_at, "
            f"last_observed_at FROM {_BROWSER_FAILURE_GROUPS} "
            "ORDER BY last_observed_at DESC, signature, latest_event_id DESC, kind, provenance LIMIT ?",
            (MAX_BROWSER_FAILURE_FINGERPRINTS,),
        ).fetchall()
        retained, failures, errors, rejections, confirmed_real, controlled_probe, unknown = map(
            int, summary[:7],
        )
        latest = float(summary[7]) if summary[7] is not None else None
        fingerprints = tuple(
            {
                "signature": str(item[1]),
                "kind": str(item[0]),
                "provenance": str(item[2]),
                "count": int(item[3]),
                "first_observed_at": float(item[4]),
                "last_observed_at": float(item[5]),
                "code_revisions": tuple(
                    str(row[0])
                    for row in connection.execute(
                        f"SELECT code_revision FROM {_BROWSER_FAILURE_REVISIONS} "
                        "WHERE kind = ? AND signature = ? AND provenance = ? "
                        "ORDER BY code_revision",
                        item[:3],
                    )
                ),
                "state": "open",
                "state_reason": "no durable closure or path-execution evidence",
            }
            for item in failure_groups
        )
        return {
            "retained_observations": retained,
            "retained_failures": failures,
            "confirmed_real_failures": confirmed_real,
            "probe_failures": controlled_probe,
            "unknown_failures": unknown,
            "retained_errors": errors,
            "retained_unhandled_rejections": rejections,
            "last_retained_observed_at": latest,
            "last_retained_observed_age_seconds": round(max(0.0, now - latest), 3) if latest is not None else None,
            "fingerprints": fingerprints,
            "classification_counts": {"open": len(fingerprints), "fixed": 0, "live_verified": 0},
            "unprovable_states": ("fixed", "live_verified"),
        }

    @contextmanager
    def pinned_snapshot(
        self,
        *,
        dirty_intervals: Iterable[tuple[int | float, int | float]] | None = None,
        include_coverage: bool = True,
    ) -> Iterator[Callable[[], StoreSnapshot]]:
        """Pin one WAL generation before yielding its potentially longer row scan."""

        connection = self._connection()
        intervals = _coalesced_dirty_intervals(dirty_intervals)
        time_clauses = _time_clauses(intervals)
        with _transaction(connection):
            header = _read_header(connection)

            def read() -> StoreSnapshot:
                observation_rows = tuple(
                    row
                    for time_clause, time_parameters in time_clauses
                    for row in connection.execute(
                        "SELECT event_id, family, source_id, observed_at, epoch_id, "
                        "owner_generation, payload_json FROM observations" + time_clause
                        + " ORDER BY observed_at, family, source_id",
                        time_parameters,
                    ).fetchall()
                )
                observation_rows = _rows_in_dirty_intervals(
                    observation_rows, intervals, 3,
                )
                coverage_rows = () if not include_coverage else connection.execute(
                    "SELECT family, source_id, epoch_id, started_at, ended_at, "
                    "native_cadence_seconds, owner_generation FROM coverage_epochs "
                    "ORDER BY started_at, family, source_id, epoch_id"
                ).fetchall()
                usage_rows = tuple(
                    row
                    for time_clause, time_parameters in time_clauses
                    for row in connection.execute(
                        "SELECT event_id, direction, modality, cache_role, unit, "
                        "observed_at, payload_json FROM usage_atoms" + time_clause
                        + " ORDER BY observed_at, event_id, direction, modality, cache_role, unit",
                        time_parameters,
                    ).fetchall()
                )
                usage_rows = _rows_in_dirty_intervals(
                    usage_rows, intervals, 5,
                )
                unavailable_rows = () if not include_coverage else connection.execute(
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

    def last_pruned_at(self) -> float:
        """Return the last successful prune, or 0.0 when this store never pruned.

        A never-pruned store reads as due: the nightly schedule then runs once,
        promptly, instead of waiting a whole day with an unbounded database.
        """

        try:
            value = read_json_file(
                self.path.parent / PRUNE_STATE_FILENAME, None, exceptions=(FileNotFoundError,)
            )
        except (OSError, json.JSONDecodeError):
            # Maintenance metadata, not facts. An unreadable sidecar means "the
            # last prune is unknown", which is due -- never "skip the prune".
            return 0.0
        if not isinstance(value, dict):
            return 0.0
        recorded = value.get("last_pruned_at")
        if isinstance(recorded, bool) or not isinstance(recorded, (int, float)):
            return 0.0
        if not math.isfinite(float(recorded)) or float(recorded) < 0:
            return 0.0
        return float(recorded)

    def _record_pruned_at(self, now: float) -> None:
        atomic_write_text(
            self.path.parent / PRUNE_STATE_FILENAME,
            json.dumps({"last_pruned_at": now}, sort_keys=True, separators=(",", ":")) + "\n",
            mode=0o600,
        )

    def prune(self, *, now: float) -> PruneResult:
        if self.read_only:
            raise StatsCurrentError("stats store reader cannot mutate the database")
        _require_retention_covers_display_window()
        cutoff = _validate_timestamp(now, "now") - RETENTION_SECONDS
        connection = self._connection()
        with _transaction(connection):
            _prune_browser_diagnostics(connection, cutoff)
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
        # Recorded only after the transaction commits: a prune that failed must
        # stay due, or one bad night silently becomes a skipped day.
        self._record_pruned_at(now)
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


def _truncate_wal(connection: sqlite3.Connection) -> None:
    result = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    if result is None or len(result) != 3 or int(result[0]) != 0:
        raise StatsCurrentError("stats WAL truncate remained busy")


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
