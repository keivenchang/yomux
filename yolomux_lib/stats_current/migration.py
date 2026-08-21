# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Atomic conversion of retired YO!stats sources into the current store."""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import sqlite3
import tempfile
import time
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

from yolomux_lib.atomic_file import atomic_write_text
from yolomux_lib.atomic_file import file_lock
from yolomux_lib.filesystem.io_ops import read_json_file

from . import identity
from . import materializer
from .families import FAMILY_BY_NAME
from .families import FamilyValidationError
from .families import validate_payload
from .storage import APPLICATION_ID
from .storage import DATABASE_FILENAME
from .storage import MIN_WRITER_BUILD
from .storage import MIN_WRITER_PROTOCOL
from .storage import SCHEMA_VERSION
from .storage import CoverageEpoch
from .storage import MigrationReconciliation
from .storage import Observation
from .storage import RETENTION_SECONDS
from .storage import SchemaMismatchError
from .storage import StatsCurrentError
from .storage import StoreSnapshot
from .storage import Store
from .storage import UnavailableSpan
from .storage import UsageAtom
from .storage import WRITER_FENCE_FILENAME
from .storage import require_compatible_writer
from .storage import normalize_unavailable_spans
from .usage import UsageValidationError
from .usage import normalize_usage_atom


MIGRATION_ID = "stats-v5-current-only-v1"
# One name for the recovered-from-corruption outcome, shared by the migration that
# records it, the service that reports it, and the gate that asserts it.
UNREADABLE_CURRENT_DATABASE = "unreadable_current_database"
# Bounded so a directory that somehow cannot yield a free name fails loudly
# rather than spinning.
QUARANTINE_NAME_ATTEMPTS = 1_000
RETIRED_DATABASE_FILENAME = "stats-history.sqlite3"
RETIRED_JSON_FILENAMES = (
    "stats-client-history-v4.json",
    "stats-client-history-v3.json",
    "stats-client-history.json",
    "tmux-AI-status.json",
)
RETIREMENT_ARCHIVE_FILENAME = ".stats-v5-retirement.zip"
RETIREMENT_JOURNAL_FILENAME = ".stats-v5-retirement.json"
RETIREMENT_MANIFEST_MEMBER = "manifest.json"
RETIREMENT_FORMAT = 1
RETIRED_SCHEMA_VERSIONS = frozenset({2, 3, 4})
V5_DATABASE_FILENAME = "stats-v5.sqlite3"
V5_SCHEMA_VERSION = 5
# Schema 5 was released with this writer fence.  A schema-5 database is copied
# into the current store and then upgraded to the current fence; it must not be
# rejected merely because the current daemon build has advanced.
V5_MIN_WRITER_PROTOCOL = 24
V5_MIN_WRITER_BUILD = 3
V5_SCHEMA_META_COLUMNS = (
    "singleton", "minimum_writer_protocol", "minimum_writer_build", "source_generation",
)
V7_DATABASE_FILENAME = "stats-v7.sqlite3"
V7_SCHEMA_VERSION = 7
# Schema 7's fence. A v7 database is copied into the v8 store and then upgraded to the current
# fence; it must not be rejected merely because the daemon build has advanced past it.
V7_MIN_WRITER_PROTOCOL = 24
V7_MIN_WRITER_BUILD = 6
# The FACT tables, which are byte-identical between v7 and v8. Schema 8 added only ring-extension
# objects, so every fact identity, payload and generation crosses unchanged and must not be
# re-materialized: re-deriving them would be slow and could change identities.
V7_FACT_TABLE_COLUMNS = {
    "observations": (
        "event_id", "family", "source_id", "observed_at", "epoch_id", "owner_generation",
        "payload_json",
    ),
    "coverage_epochs": (
        "family", "source_id", "epoch_id", "started_at", "ended_at",
        "native_cadence_seconds", "owner_generation",
    ),
    "unavailable_spans": (
        "family", "source_id", "epoch_id", "started_at", "ended_at",
        "native_cadence_seconds", "reason", "owner_generation",
    ),
    "usage_atoms": (
        "event_id", "direction", "modality", "cache_role", "unit", "observed_at", "payload_json",
    ),
    "migration_reconciliation": (
        "migration_id", "completed_at", "source_digest", "details_json",
    ),
}
# Explicit copy budgets. A single `INSERT INTO ... SELECT` would stream inside SQLite but is
# unbounded and unobservable: nothing caps how much it holds or reports how much it moved. Copying
# in keyed chunks bounds the transaction and yields a per-table row and byte accounting that the
# resource gates can assert against.
V8_MIGRATION_ROW_CHUNK = 2_000
V8_MIGRATION_BYTE_CHUNK = 8 * 1024 * 1024
# The six interruption points the migration must survive. Named rather than positional so a test
# asserts the boundary it means, and so an added boundary cannot silently renumber the others.
V8_MIGRATION_BOUNDARIES = (
    "before_shadow_creation",
    "after_shadow_creation",
    "during_population",
    "before_validation",
    "before_cutover",
    "after_commit",
)
V5_TABLE_COLUMNS = {
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
    "schema_meta": V5_SCHEMA_META_COLUMNS,
    "unavailable_spans": (
        "family", "source_id", "epoch_id", "started_at", "ended_at",
        "native_cadence_seconds", "reason", "owner_generation",
    ),
    "usage_atoms": (
        "event_id", "direction", "modality", "cache_role", "unit", "observed_at", "payload_json",
    ),
}
LEGACY_SERVER_FIELDS = (
    "cpu_total_percent", "cpu_count", "system_cpu_total_percent", "system_cpu_count",
    "ask_agent_total", "run_agent_total", "transition_agent_total", "idle_agent_total",
    "active_agent_total", "inactive_agent_total", "agent_activity_samples",
    "tokens_per_agent_total", "agent_token_samples",
)
SUPPORTED_LEGACY_TABLES = frozenset({
    "schema_meta", "stats_buckets", "stats_coverage_intervals",
    "stats_raw_samples", "stats_usage_atoms", "stats_clients", "stats_processes",
    "stats_agent_rates", "stats_host_metrics", "stats_rollups",
})


class MigrationError(RuntimeError):
    """The current database was not activated."""


class UnreadableCurrentDatabase(MigrationError):
    """The current database cannot be opened or read at all.

    Only physical damage lands here -- a torn page, a truncated file, a killed
    checkpoint. It is recoverable by quarantine and rebuild. A database that
    opens fine but carries reconciliation metadata that does not add up is a
    different failure: that stays a plain MigrationError and stays fatal, so
    tampering is never silently rewritten away.
    """


@dataclass(frozen=True)
class MigrationInputs:
    state_dir: Path
    legacy_database: Path | None = None
    usage_atoms: tuple[UsageAtom, ...] = ()


@dataclass(frozen=True)
class MigrationIssue:
    kind: str
    source: str
    detail: str

    def to_json(self) -> dict[str, str]:
        return {"kind": self.kind, "source": self.source, "detail": self.detail}


@dataclass(frozen=True)
class MigrationReport:
    active_database: Path
    source_digest: str
    observations: int
    coverage_epochs: int
    usage_atoms: int
    unavailable_spans: int
    issues: tuple[MigrationIssue, ...]
    issue_count: int
    already_active: bool = False


@dataclass
class _Recovered:
    observations: dict[tuple[str, str, str], Observation] = field(default_factory=dict)
    coverage: dict[tuple[str, str, str], CoverageEpoch] = field(default_factory=dict)
    usage: dict[tuple[str, str, str, str, str], tuple[int, UsageAtom]] = field(default_factory=dict)
    unavailable: dict[tuple[str, str, str, float, float], UnavailableSpan] = field(default_factory=dict)
    issues: list[MigrationIssue] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    database_buckets: bool = False
    client_bucket_keys: set[tuple[int, int]] = field(default_factory=set)
    imported_spools: set[Path] = field(default_factory=set)
    canonicalized_identities: set[tuple[str, str]] = field(default_factory=set)


def _canonicalize_legacy_identity(
    recovered: _Recovered,
    value: object,
    scope: str,
    *,
    maximum_bytes: int = identity.MAX_SERIES_COMPONENT_BYTES,
) -> str:
    normalized, changed = identity.legacy_identity(
        value, scope, maximum_bytes=maximum_bytes,
    )
    marker = (scope, normalized)
    if changed and marker not in recovered.canonicalized_identities:
        recovered.canonicalized_identities.add(marker)
        recovered.issues.append(MigrationIssue(
            "identity_canonicalized", scope, normalized,
        ))
    return normalized


@dataclass(frozen=True)
class _RetiredArtifact:
    relative_path: str
    action: str
    sha256: str
    size: int
    mode: int
    mtime_ns: int


@dataclass(frozen=True)
class _RetirementPlan:
    state_dir: Path
    artifacts: tuple[_RetiredArtifact, ...]

    @property
    def archive_path(self) -> Path:
        return self.state_dir / RETIREMENT_ARCHIVE_FILENAME

    @property
    def journal_path(self) -> Path:
        return self.state_dir / RETIREMENT_JOURNAL_FILENAME


def _v7_migration_source(state_dir: Path) -> Path | None:
    """The released v7 store to migrate, or None when there is nothing to migrate.

    Its own function so the startup dispatch has one seam a test can bypass. A dispatch assertion
    with no bypass control proves only that the code agrees with itself: deleting the v7 arm from
    `migrate()` would leave it building an empty v8 and the suite still green.
    """
    candidate = state_dir / V7_DATABASE_FILENAME
    return candidate if candidate.is_file() else None


def migrate(
    inputs: MigrationInputs,
    active_database: Path | None = None,
    *,
    completed_at: float | None = None,
) -> MigrationReport:
    """Build, reconcile, and atomically activate one dedicated current database."""

    state_dir = Path(inputs.state_dir)
    finished_at = _timestamp(time.time() if completed_at is None else completed_at, "completed_at")
    target = Path(active_database or state_dir / DATABASE_FILENAME)
    legacy = Path(inputs.legacy_database or state_dir / RETIRED_DATABASE_FILENAME)
    if target.name != DATABASE_FILENAME:
        raise MigrationError(f"current database must be named {DATABASE_FILENAME}")
    state_root = state_dir.resolve(strict=False)
    if target.parent.resolve(strict=False) != state_root:
        raise MigrationError("current database must be inside the declared state directory")
    if legacy.parent.resolve(strict=False) != state_root or legacy.is_symlink():
        raise MigrationError("retired database must be a regular state-directory input")
    if target.resolve(strict=False) == legacy.resolve(strict=False):
        raise MigrationError("current and retired database paths must be different")
    # This is deliberately before temporary-directory creation or legacy input
    # inspection. A newer runner's state fence is a terminal compatibility
    # result, including when its versioned database is temporarily absent.
    try:
        require_compatible_writer(target)
    except SchemaMismatchError:
        # A file that cannot be identified as a compatible database belongs to
        # the recovery block below. The distinct newer-writer exception still
        # propagates before any source inspection or mutation.
        pass
    recovered_active = _recover_interrupted_retirement(state_dir, target)
    if recovered_active is not None:
        return recovered_active
    unreadable_current: list[MigrationIssue] = []
    if target.exists():
        try:
            report = _active_report(target)
        except UnreadableCurrentDatabase as error:
            # A current database that cannot be read is an outcome, not a crash.
            # Killing the writer mid-checkpoint leaves exactly this state, and it
            # never heals on its own, so exiting here would make every restart die
            # identically and leave the browser retrying a request that cannot
            # succeed. Set the damaged file aside for forensics -- never delete it,
            # it is the operator's history -- and rebuild from the surviving
            # sources, carrying a typed issue so the UI can say history was reset.
            quarantined = _quarantine_database(target, "corrupt")
            unreadable_current.append(MigrationIssue(
                UNREADABLE_CURRENT_DATABASE, quarantined.name, str(error)[:256],
            ))
        else:
            return _retire_sources_beside_existing_current(state_dir, legacy, target, report)
    # Newest released format first. A v7 store is the CURRENT history of a running build; a v5 file
    # beside it is a leftover from an earlier one. Checking v5 first would let a stale artifact
    # shadow live history, and adding v7 after v5 would have exactly that effect.
    v7_source = _v7_migration_source(state_dir)
    if v7_source is not None:
        return migrate_current_v7_database(state_dir, target, v7_source, now=finished_at)
    v5_source = state_dir / V5_DATABASE_FILENAME
    if v5_source.is_file():
        return _migrate_current_v5_database(state_dir, target, v5_source)
    target.parent.mkdir(parents=True, exist_ok=True)
    recovered = _Recovered()
    for issue in unreadable_current:
        recovered.issues.append(issue)
        recovered.sources.append(issue.source)
    digest = hashlib.sha256()
    with tempfile.TemporaryDirectory(prefix=".stats-v5-migration-", dir=target.parent) as temporary:
        work = Path(temporary)
        if legacy.is_file():
            copied = _copy_database(legacy, work / "source", digest)
            try:
                _read_database(copied, recovered, digest, work, state_dir)
            except MigrationError as error:
                quarantined = _quarantine_database(legacy, "unsupported")
                recovered.sources.append(quarantined.name)
                recovered.issues.append(MigrationIssue(
                    "unsupported_legacy_database",
                    quarantined.name,
                    str(error)[:256],
                ))
        for path in (state_dir / name for name in RETIRED_JSON_FILENAMES):
            if path.is_symlink():
                raise MigrationError(f"retired JSON cannot be a symbolic link: {path.name}")
            if path.is_file():
                _read_json_source(path, recovered, digest)
        _read_remaining_spools(state_dir, recovered, digest, work)
        for atom in inputs.usage_atoms:
            try:
                normalized_atom = normalize_usage_atom(atom)
            except UsageValidationError as error:
                raise MigrationError(f"supplied usage atom cannot be converted: {error}") from error
            _add_usage(recovered, normalized_atom, 3, "supplied usage atom")
            digest.update(_atom_bytes(normalized_atom))
        _retain_current_window(recovered, finished_at)
        recovered.unavailable = {
            (
                item.family, item.source_id, item.epoch_id,
                item.started_at, item.ended_at,
            ): item
            for item in normalize_unavailable_spans(recovered.unavailable.values())
        }
        retirement = _build_retirement_plan(state_dir, legacy)
        source_digest = digest.hexdigest()
        shadow = work / "shadow" / DATABASE_FILENAME
        with Store.open(shadow) as store:
            result = store.append_batch(
                observations=recovered.observations.values(),
                coverage_epochs=recovered.coverage.values(),
                usage_atoms=(item[1] for item in recovered.usage.values()),
                unavailable_spans=recovered.unavailable.values(),
            )
            issue_counts = Counter(issue.kind for issue in recovered.issues)
            details: dict[str, object] = {
                "format": 1,
                "sources": sorted(recovered.sources),
                "counts": {
                    "observations": len(recovered.observations),
                    "coverage_epochs": len(recovered.coverage),
                    "usage_atoms": len(recovered.usage),
                    "unavailable_spans": len(recovered.unavailable),
                },
                "issue_counts": dict(sorted(issue_counts.items())),
                "issues": [issue.to_json() for issue in recovered.issues[:100]],
                "issues_truncated": max(0, len(recovered.issues) - 100),
                "retirement": {
                    "artifacts": len(retirement.artifacts),
                    "bytes": sum(item.size for item in retirement.artifacts),
                    "shared_history_rewrites": sum(
                        item.action == "rewrite_shared" for item in retirement.artifacts
                    ),
                },
            }
            store.record_migration_reconciliation(MigrationReconciliation(
                MIGRATION_ID,
                finished_at,
                source_digest,
                details,
            ))
            snapshot = store.read_snapshot()
            expected = (
                len(recovered.observations), len(recovered.coverage),
                len(recovered.usage), len(recovered.unavailable),
            )
            actual = (
                len(snapshot.observations), len(snapshot.coverage_epochs),
                len(snapshot.usage_atoms), len(snapshot.unavailable_spans),
            )
            if actual != expected or result.source_generation != (1 if any(expected) else 0):
                raise MigrationError(f"shadow reconciliation failed: expected {expected}, found {actual}")
            _validate_materializations(snapshot, result.source_generation, finished_at)
        _validate_database(shadow)
        _compact_database(shadow)
        _validate_database(shadow)
        # The service singleton lock prevents a competing writer in production;
        # this second read-only check also makes direct migration callers fail
        # closed if a newer owner appeared while the shadow was being built.
        require_compatible_writer(target)
        if target.exists():
            raise MigrationError("current database appeared during migration")
        _prepare_retirement_archive(retirement)
        try:
            _retire_legacy_sources(retirement)
            _write_retirement_journal(retirement, "activating")
            _activate_database(shadow, target)
            _fsync_directory(target.parent)
            # Activation owns the first current fence publication. This open
            # also proves the compacted file can be reopened for current writes.
            with Store.open(target):
                pass
            _active_report(target)
            _write_retirement_journal(retirement, "activated")
            _remove_retirement_journal(retirement)
            _discard_retirement_archive(retirement)
        except (OSError, sqlite3.Error, StatsCurrentError, MigrationError, zipfile.BadZipFile) as error:
            _rollback_retirement(retirement, target, error)
            raise MigrationError(f"current activation/retirement failed: {type(error).__name__}") from error
    report = _active_report(target)
    return MigrationReport(
        report.active_database, source_digest, report.observations, report.coverage_epochs,
        report.usage_atoms, report.unavailable_spans, tuple(recovered.issues),
        len(recovered.issues), False,
    )


def _migrate_current_v5_database(state_dir: Path, target: Path, source: Path) -> MigrationReport:
    """Atomically copy the frozen schema-5 current store into the schema-7 store.

    Schema 5 is a former *current* database, not a retired aggregate format.  Keep this narrow
    copier separate from `_read_database`: all fact tables already have the exact current shape, so
    re-materializing them would be slow and could change identities.  The new metadata column starts
    at zero, causing the first eligible idle maintenance cycle to compact the v6 file.
    """
    with tempfile.TemporaryDirectory(prefix=".stats-v6-migration-", dir=target.parent) as temporary:
        work = Path(temporary)
        source_digest = hashlib.sha256()
        copied_source = _copy_database(source, work / "source", source_digest)
        _validate_current_v5_database(copied_source)
        shadow = work / DATABASE_FILENAME
        with Store.open(shadow) as store:
            connection = store._connection()
            connection.execute("ATTACH DATABASE ? AS source_v5", (str(copied_source),))
            try:
                connection.execute("BEGIN IMMEDIATE")
                for table in (
                    "observations", "coverage_epochs", "unavailable_spans", "usage_atoms",
                    "migration_reconciliation",
                ):
                    columns = ", ".join(V5_TABLE_COLUMNS[table])
                    connection.execute(
                        f"INSERT INTO {table}({columns}) SELECT {columns} FROM source_v5.{table}"
                    )
                source_generation = int(connection.execute(
                    "SELECT source_generation FROM source_v5.schema_meta WHERE singleton = 1"
                ).fetchone()[0])
                connection.execute(
                    "UPDATE schema_meta SET minimum_writer_protocol = ?, minimum_writer_build = ?, "
                    "source_generation = ?, last_vacuumed_at = 0 WHERE singleton = 1",
                    (MIN_WRITER_PROTOCOL, MIN_WRITER_BUILD, source_generation),
                )
                connection.execute("COMMIT")
            except sqlite3.Error:
                connection.execute("ROLLBACK")
                raise
            finally:
                connection.execute("DETACH DATABASE source_v5")
            Store._validate_schema_shape(connection)
        _validate_database(shadow)
        _compact_database(shadow)
        _validate_database(shadow)
        require_compatible_writer(target)
        if target.exists():
            raise MigrationError("current database appeared during schema-5 migration")
        _activate_database(shadow, target)
        _fsync_directory(target.parent)
        with Store.open(target):
            pass
    report = _active_report(target)
    return MigrationReport(
        report.active_database,
        source_digest.hexdigest(),
        report.observations,
        report.coverage_epochs,
        report.usage_atoms,
        report.unavailable_spans,
        (MigrationIssue("current_schema", source.name, str(V5_SCHEMA_VERSION)),),
        1,
        False,
    )


def _validate_current_v5_database(path: Path) -> None:
    """Validate the exact frozen v5 shape before any v6 shadow is created."""
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(f"file:{quote(str(path))}?mode=ro", uri=True)
        if int(connection.execute("PRAGMA application_id").fetchone()[0]) != APPLICATION_ID:
            raise MigrationError("schema-5 source has the wrong application id")
        if int(connection.execute("PRAGMA user_version").fetchone()[0]) != V5_SCHEMA_VERSION:
            raise MigrationError("schema-5 source has the wrong schema version")
        tables = {
            str(row[0]) for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        if tables != set(V5_TABLE_COLUMNS):
            raise MigrationError("schema-5 source has an unexpected table set")
        columns = {
            table: tuple(str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})"))
            for table in tables
        }
        if columns != V5_TABLE_COLUMNS:
            raise MigrationError("schema-5 source has an unexpected table shape")
        row = connection.execute(
            "SELECT minimum_writer_protocol, minimum_writer_build, source_generation "
            "FROM schema_meta WHERE singleton = 1"
        ).fetchone()
        if (
            row is None
            or int(row[0]) != V5_MIN_WRITER_PROTOCOL
            or int(row[1]) != V5_MIN_WRITER_BUILD
            or int(row[2]) < 0
        ):
            raise MigrationError("schema-5 source has incompatible writer metadata")
    except (OSError, sqlite3.Error, ValueError) as error:
        raise MigrationError(f"schema-5 source cannot be read: {error}") from error
    finally:
        if connection is not None:
            connection.close()


def _active_report(path: Path) -> MigrationReport:
    try:
        # Existing current databases are audited without publishing a fence,
        # changing journal mode, or otherwise mutating an idempotent restart.
        with Store.open_reader(path, include_browser_diagnostics=False) as store:
            schema, reconciliations = store.read_migration_state()
    except (OSError, sqlite3.Error, StatsCurrentError, ValueError) as error:
        raise UnreadableCurrentDatabase(f"active current database is invalid: {error}") from error
    records = [item for item in reconciliations if item.migration_id == MIGRATION_ID]
    if len(records) != 1:
        raise MigrationError("active current database has no completed migration reconciliation")
    record = records[0]
    if record.details.get("format") != 1:
        raise MigrationError("active current database has an unsupported migration reconciliation")
    expected_count_names = {
        "observations", "coverage_epochs", "usage_atoms", "unavailable_spans",
    }
    stored_counts = record.details.get("counts")
    if not isinstance(stored_counts, dict) or set(stored_counts) != expected_count_names:
        raise MigrationError("active current database has malformed migration counts")
    expected_counts = tuple(
        _nonnegative_count(stored_counts[name], f"migration count {name}")
        for name in ("observations", "coverage_epochs", "usage_atoms", "unavailable_spans")
    )
    activation_generation = 1 if any(expected_counts) else 0
    if schema.source_generation == activation_generation:
        try:
            with Store.open_reader(path, include_browser_diagnostics=False) as store:
                snapshot = store.read_snapshot()
        except (OSError, sqlite3.Error, StatsCurrentError, ValueError) as error:
            raise UnreadableCurrentDatabase(
                f"active current database is invalid: {error}"
            ) from error
        actual_counts = (
            len(snapshot.observations),
            len(snapshot.coverage_epochs),
            len(snapshot.usage_atoms),
            len(snapshot.unavailable_spans),
        )
        if expected_counts != actual_counts:
            raise MigrationError(
                f"active migration reconciliation counts {expected_counts} do not match {actual_counts}"
            )
    raw_issues = record.details.get("issues")
    if not isinstance(raw_issues, list) or len(raw_issues) > 100:
        raise MigrationError("active current database has malformed migration issues")
    issue_counts = record.details.get("issue_counts")
    if not isinstance(issue_counts, dict) or any(not isinstance(key, str) for key in issue_counts):
        raise MigrationError("active current database has malformed migration issue counts")
    issue_count = sum(
        _nonnegative_count(value, f"migration issue count {key}")
        for key, value in issue_counts.items()
    )
    truncated = _nonnegative_count(
        record.details.get("issues_truncated"), "migration truncated issue count",
    )
    if len(raw_issues) + truncated != issue_count:
        raise MigrationError("active migration issue totals do not reconcile")
    sources = record.details.get("sources")
    if not isinstance(sources, list) or any(not isinstance(item, str) for item in sources):
        raise MigrationError("active current database has malformed migration sources")
    if len(record.source_digest) != 64 or any(char not in "0123456789abcdef" for char in record.source_digest):
        raise MigrationError("active current database has malformed migration digest")
    issues = tuple(
        MigrationIssue(str(item.get("kind") or ""), str(item.get("source") or ""), str(item.get("detail") or ""))
        for item in raw_issues if isinstance(item, dict) and set(item) == {"kind", "source", "detail"}
    )
    if len(issues) != len(raw_issues):
        raise MigrationError("active current database has malformed migration issue details")
    return MigrationReport(
        path, record.source_digest, *expected_counts, issues,
        issue_count, True,
    )


def _nonnegative_count(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MigrationError(f"{name} must be a non-negative integer")
    return value


def _copy_database(source: Path, destination_dir: Path, digest: Any) -> Path:
    destination_dir.mkdir(parents=True, exist_ok=True)
    target = destination_dir / source.name
    for suffix in ("", "-wal"):
        candidate = Path(f"{source}{suffix}")
        if not candidate.is_file():
            continue
        before = candidate.stat()
        copied = Path(f"{target}{suffix}")
        shutil.copy2(candidate, copied)
        after = candidate.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise MigrationError(f"source changed while copying: {candidate}")
        _hash_file(digest, candidate.name, copied)
    return target


def _free_quarantine_target(source: Path, label: str) -> Path:
    """Pick a quarantine name that collides with nothing already on disk.

    The stamp is time_ns plus pid, which a backwards clock step can repeat. If a
    collision were treated as an error it would propagate out of the recovery
    path and kill the very daemon this recovery exists to keep alive, so the name
    is made unique instead. An existing quarantine is never reused or overwritten
    -- it is somebody's history.
    """

    base = source.with_name(f"{source.name}.{label}-{time.time_ns()}-{os.getpid()}")
    candidate = base
    for index in range(1, QUARANTINE_NAME_ATTEMPTS):
        if not any(
            Path(f"{candidate}{suffix}").exists() or Path(f"{candidate}{suffix}").is_symlink()
            for suffix in ("", "-wal", "-shm", "-journal")
        ):
            return candidate
        candidate = Path(f"{base}.{index}")
    raise MigrationError(f"no free {label} database quarantine name beside {source.name}")


def _quarantine_database(source: Path, label: str) -> Path:
    """Move a database and its sidecars aside under a stamped name, never deleting them."""

    target = _free_quarantine_target(source, label)
    moved = False
    for suffix in ("", "-wal", "-shm", "-journal"):
        candidate = Path(f"{source}{suffix}")
        if not candidate.exists() and not candidate.is_symlink():
            continue
        if candidate.is_symlink() or not candidate.is_file():
            raise MigrationError(
                f"{label} database artifact is not a regular file: {candidate.name}"
            )
        os.replace(candidate, Path(f"{target}{suffix}"))
        moved = True
    if not moved:
        raise MigrationError(f"{label} database disappeared before quarantine")
    _fsync_directory(source.parent)
    return target


def _read_database(
    path: Path,
    recovered: _Recovered,
    digest: Any,
    work: Path,
    state_dir: Path,
) -> None:
    try:
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise MigrationError("retired database integrity check failed")
        tables = {
            str(row[0]) for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        unknown = tables - SUPPORTED_LEGACY_TABLES
        if unknown:
            raise MigrationError(f"unsupported retired database tables: {sorted(unknown)}")
        if "schema_meta" not in tables:
            raise MigrationError("retired database has no schema_meta table")
        application_id = int(connection.execute("PRAGMA application_id").fetchone()[0])
        user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if application_id != 0 or user_version != 0:
            raise MigrationError(
                f"unsupported retired database application/user version {application_id:#x}/{user_version}"
            )
        metadata = {
            str(row[0]): str(row[1])
            for row in connection.execute("SELECT key,value FROM schema_meta")
        }
        try:
            schema = int(metadata.get("schema_version", ""))
        except ValueError as error:
            raise MigrationError("retired database schema_meta.schema_version is invalid") from error
        if schema not in RETIRED_SCHEMA_VERSIONS:
            raise MigrationError(f"unsupported retired database schema_meta version {schema}")
        recovered.sources.append(path.name)
        recovered.issues.append(MigrationIssue(
            "retired_schema", path.name, str(schema),
        ))
        if "stats_usage_atoms" in tables:
            for row in connection.execute(
                "SELECT event_id,direction,modality,cache_role,unit,sample_time,atom_json "
                "FROM stats_usage_atoms"
            ):
                _recover_component(json.loads(str(row[6])), recovered, "stats_usage_atoms", 2, row)
        recovered.issues.extend(
            MigrationIssue("retired_marker", "schema_meta", key)
            for key in sorted(metadata)
            if key not in {"schema_version", "minimum_writer_protocol", "minimum_writer_build"}
        )
        _read_spool(
            metadata.get("agent_token_atom_spool", ""), recovered, digest, work, state_dir,
        )
        if "stats_coverage_intervals" in tables:
            _read_coverage(connection, recovered)
        if "stats_raw_samples" in tables:
            _read_raw_samples(connection, recovered)
        bucket_count = 0
        if "stats_buckets" in tables:
            for row in connection.execute("SELECT start,duration,bucket_json FROM stats_buckets"):
                bucket = _json_object(row[2], "stats_buckets.bucket_json")
                bucket["start"], bucket["duration"] = int(row[0]), int(row[1])
                _recover_bucket(bucket, recovered, "stats_buckets")
                bucket_count += 1
        recovered.database_buckets = bucket_count > 0
        for table in sorted(tables & {"stats_clients", "stats_processes", "stats_agent_rates", "stats_host_metrics"}):
            recovered.issues.append(MigrationIssue("duplicate_side_table", table, "bucket_json remains the owner"))
        if "stats_rollups" in tables:
            recovered.issues.append(MigrationIssue("derived_table", "stats_rollups", "not imported"))
        recovered.issues.append(MigrationIssue("database_buckets", path.name, str(bucket_count)))
    except (OSError, sqlite3.Error, json.JSONDecodeError, ValueError) as error:
        raise MigrationError(f"retired database cannot be converted: {error}") from error
    finally:
        if "connection" in locals():
            connection.close()


def _read_coverage(connection: sqlite3.Connection, recovered: _Recovered) -> None:
    rows = connection.execute(
        "SELECT family,epoch_id,start,end,cadence,owner_generation,source "
        "FROM stats_coverage_intervals"
    )
    for row in rows:
        family = str(row[0])
        if family in {"raw", "cost"}:
            continue
        if family not in FAMILY_BY_NAME:
            recovered.issues.append(MigrationIssue("unsupported_family", family, "coverage interval"))
            continue
        source = _canonicalize_legacy_identity(
            recovered, f"retired-coverage:{str(row[6] or 'sampler')}", "source",
        )
        epoch = _canonicalize_legacy_identity(recovered, str(row[1]), "epoch")
        item = CoverageEpoch(
            family, source, epoch, float(row[2]), float(row[3]),
            float(row[4]), max(0, int(row[5])),
        )
        recovered.coverage[(item.family, item.source_id, item.epoch_id)] = item


def _read_raw_samples(connection: sqlite3.Connection, recovered: _Recovered) -> None:
    for row in connection.execute(
        "SELECT family,source_id,sample_time,epoch_id,owner_generation,payload_json "
        "FROM stats_raw_samples"
    ):
        family, observed = str(row[0]), float(row[2])
        payload = _json_object(row[5], "stats_raw_samples.payload_json")
        source = str(row[1] or "retired-raw")
        try:
            validate_payload(family, payload)
        except FamilyValidationError:
            _recover_bucket(
                {"start": observed, "duration": _native_cadence(family), **payload},
                recovered,
                f"stats_raw_samples:{source}",
            )
            continue
        _add_observation(
            recovered, family, source, observed, payload,
            str(row[3] or f"retired-raw:{observed}"), max(0, int(row[4])),
            f"raw:{family}:{source}:{observed}",
        )


def _read_spool(
    raw: str,
    recovered: _Recovered,
    digest: Any,
    work: Path,
    state_dir: Path,
) -> None:
    try:
        value = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        recovered.issues.append(MigrationIssue("unreadable_spool_pointer", "schema_meta", "invalid JSON"))
        return
    source = Path(str(value.get("path") or "")) if isinstance(value, dict) else None
    if source is None:
        return
    expected_parent = (state_dir / "services").resolve(strict=False)
    valid_name = (
        source.name.startswith("statsd-agent-token-scan-")
        and source.name.endswith(".atoms.sqlite3")
    )
    if not valid_name or source.is_symlink() or source.parent.resolve(strict=False) != expected_parent:
        raise MigrationError("token spool path is outside the expected state services directory")
    if not source.is_file():
        return
    _read_spool_path(source, recovered, digest, work)


def _read_remaining_spools(
    state_dir: Path,
    recovered: _Recovered,
    digest: Any,
    work: Path,
) -> None:
    services = state_dir / "services"
    if not services.is_dir():
        return
    for source in sorted(services.glob("statsd-agent-token-scan-*.atoms.sqlite3")):
        if source.is_symlink():
            raise MigrationError("token spool cannot be a symbolic link")
        if source.is_file() and source not in recovered.imported_spools:
            _read_spool_path(source, recovered, digest, work)


def _read_spool_path(
    source: Path,
    recovered: _Recovered,
    digest: Any,
    work: Path,
) -> None:
    copied = _copy_database(source, work / "spool", digest)
    try:
        connection = sqlite3.connect(copied)
        tables = {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "atoms" not in tables:
            raise MigrationError("token spool has no atoms table")
        for row in connection.execute("SELECT payload FROM atoms"):
            _recover_component(json.loads(str(row[0])), recovered, source.name, 2)
        recovered.sources.append(source.name)
        recovered.imported_spools.add(source)
    except (sqlite3.Error, json.JSONDecodeError) as error:
        raise MigrationError(f"token spool cannot be converted: {error}") from error
    finally:
        if "connection" in locals():
            connection.close()


def _read_json_source(path: Path, recovered: _Recovered, digest: Any) -> None:
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MigrationError(f"retired JSON cannot be converted: {path}: {error}") from error
    if path.name == "tmux-AI-status.json":
        history = value.get("stats_history") if isinstance(value, dict) else None
        if history is None:
            return
        if not isinstance(history, dict):
            raise MigrationError(f"retired JSON stats_history is malformed: {path}")
        digest.update(path.name.encode())
        digest.update(json.dumps(history, sort_keys=True, separators=(",", ":")).encode())
        recovered.sources.append(path.name)
        if recovered.database_buckets:
            recovered.issues.append(MigrationIssue("superseded_json", path.name, "database buckets are authoritative"))
            return
        _read_json_bucket_groups(history, recovered, path.name, shared=True)
        return
    if not isinstance(value, dict) or value.get("version") not in (2, 3, 4):
        raise MigrationError(f"unsupported retired JSON version: {path}")
    digest.update(path.name.encode())
    digest.update(raw)
    recovered.sources.append(path.name)
    if recovered.database_buckets:
        recovered.issues.append(MigrationIssue("superseded_json", path.name, "database buckets are authoritative"))
        return
    _read_json_bucket_groups(value, recovered, path.name, shared=False)


def _read_json_bucket_groups(value: dict[str, Any], recovered: _Recovered, source: str, *, shared: bool) -> None:
    for group in ("raw_buckets", "rollup_buckets"):
        rows = value.get(group, [])
        if not isinstance(rows, list):
            raise MigrationError(f"{source}.{group} must be an array")
        for row in rows:
            bucket = _shared_bucket(row) if shared else _client_bucket(row)
            if bucket is None:
                raise MigrationError(f"malformed retired bucket in {source}.{group}")
            key = (int(bucket["start"]), int(bucket["duration"]))
            if not shared and key in recovered.client_bucket_keys:
                recovered.issues.append(MigrationIssue("superseded_json_bucket", source, f"{key[0]}:{key[1]}"))
                continue
            if not shared:
                recovered.client_bucket_keys.add(key)
            _recover_bucket(bucket, recovered, f"{source}:{group}")


def _client_bucket(row: object) -> dict[str, Any] | None:
    if not isinstance(row, list) or len(row) not in (4, 5):
        return None
    try:
        start, duration = int(row[0]), int(row[1])
    except (TypeError, ValueError):
        return None
    return {
        "start": start, "duration": duration,
        "clients": row[3] if isinstance(row[3], dict) else {},
        "servers": row[4] if len(row) == 5 and isinstance(row[4], dict) else {},
    }


def _shared_bucket(row: object) -> dict[str, Any] | None:
    if isinstance(row, dict):
        return dict(row)
    if not isinstance(row, list) or len(row) < 4 + len(LEGACY_SERVER_FIELDS) + 1:
        return None
    try:
        start, duration = int(row[0]), int(row[1])
    except (TypeError, ValueError):
        return None
    values = row[4:]
    return {
        "start": start,
        "duration": duration,
        **dict(zip(LEGACY_SERVER_FIELDS, values[:len(LEGACY_SERVER_FIELDS)], strict=True)),
        "agent_token_rates": values[len(LEGACY_SERVER_FIELDS)],
        "host_metrics": values[len(LEGACY_SERVER_FIELDS) + 1] if len(values) > len(LEGACY_SERVER_FIELDS) + 1 else {},
    }


def _recover_bucket(bucket: dict[str, Any], recovered: _Recovered, source: str) -> None:
    start = _finite(bucket.get("start"))
    duration = _finite(bucket.get("duration"))
    if start < 0 or duration <= 0:
        raise MigrationError(f"invalid retired bucket identity in {source}")
    _recover_cpu(bucket, recovered, source, start, duration)
    _recover_host_metrics(bucket, recovered, source, start, duration)
    if _finite(bucket.get("agent_activity_samples")):
        _add_unavailable(recovered, "agent_status", source, start, duration, "agent identities lost")
    clients = bucket.get("clients") if isinstance(bucket.get("clients"), dict) else {}
    for client, values in clients.items():
        if isinstance(values, dict) and any(_finite(values.get(name)) for name in (
            "api_count", "sse_count", "latency_count", "bandwidth_bytes", "heartbeat_count", "disconnected_ms",
        )):
            _add_unavailable(recovered, "browser", f"{source}:{client}", start, duration, "event identities lost")
    summary = bucket.get("cost_summary") if isinstance(bucket.get("cost_summary"), dict) else {}
    components = summary.get("components") if isinstance(summary.get("components"), list) else []
    failed_components = 0
    for component in components:
        if not isinstance(component, dict):
            failed_components += 1
            continue
        candidate = dict(component)
        candidate.setdefault("timestamp", start)
        if not _recover_component(candidate, recovered, source, 1):
            failed_components += 1
    token_facts = bool(_finite(bucket.get("agent_token_samples")) or bucket.get("agent_token_rates"))
    if token_facts or failed_components:
        reason = "usage identities incomplete" if failed_components else "token aggregates are not source atoms"
        _add_unavailable(recovered, "agent_tokens", source, start, duration, reason)
        _add_unavailable(recovered, "cost", source, start, duration, reason)


def _recover_cpu(bucket: dict[str, Any], recovered: _Recovered, source: str, start: float, duration: float) -> None:
    count = _finite(bucket.get("cpu_count"))
    system_count = _finite(bucket.get("system_cpu_count"))
    servers = bucket.get("servers") if isinstance(bucket.get("servers"), dict) else {}
    if not count and not servers:
        return
    if duration > 1 or system_count != 1:
        _add_unavailable(recovered, "cpu", source, start, duration, "CPU samples were aggregated")
        return
    system = _finite(bucket.get("system_cpu_total_percent"))
    candidates = {
        str(key): value for key, value in servers.items()
        if isinstance(value, dict) and _finite(value.get("cpu_count"))
    }
    if not candidates:
        candidates = {"web": {"cpu_total_percent": bucket.get("cpu_total_percent"), "cpu_count": count}}
    for key, value in candidates.items():
        samples = _finite(value.get("cpu_count"))
        if samples != 1:
            _add_unavailable(recovered, "cpu", f"{source}:{key}", start, duration, "CPU samples were aggregated")
            continue
        _add_observation(
            recovered, "cpu", f"retired:{key}", start,
            {"process_percent": _finite(value.get("cpu_total_percent")), "system_percent": system},
            f"retired:{source}:{start}", 0, f"{source}:cpu:{key}:{start}",
        )


def _recover_host_metrics(bucket: dict[str, Any], recovered: _Recovered, source: str, start: float, duration: float) -> None:
    host = bucket.get("host_metrics") if isinstance(bucket.get("host_metrics"), dict) else {}
    memory_count = _finite(host.get("system_memory_count"))
    if memory_count:
        if duration <= 60 and memory_count == 1:
            _add_observation(
                recovered, "system_memory", "retired:host", start,
                {
                    "used_bytes": _finite(host.get("system_memory_used_total_bytes")),
                    "capacity_bytes": _finite(host.get("system_memory_capacity_total_bytes")),
                },
                f"retired:{source}:{start}", 0, f"{source}:memory:{start}",
            )
        else:
            _add_unavailable(recovered, "system_memory", source, start, duration, "memory samples were aggregated")
    devices = host.get("gpu_devices") if isinstance(host.get("gpu_devices"), dict) else {}
    for key, value in devices.items():
        if not isinstance(value, dict):
            continue
        samples = _finite(value.get("samples"))
        if duration > 10 or samples != 1:
            _add_unavailable(recovered, "gpu", f"{source}:{key}", start, duration, "GPU samples were aggregated")
            continue
        _add_observation(
            recovered, "gpu", f"retired:{key}", start,
            {
                "util_percent": _finite(value.get("util_total_percent")),
                "memory_used_bytes": _finite(value.get("memory_used_total_bytes")),
                "memory_capacity_bytes": _finite(value.get("memory_capacity_total_bytes")),
                "label": str(value.get("label") or key),
            },
            f"retired:{source}:{start}", 0, f"{source}:gpu:{key}:{start}",
        )
    services = host.get("service_load") if isinstance(host.get("service_load"), dict) else {}
    for key, value in services.items():
        if not isinstance(value, dict):
            continue
        samples = _finite(value.get("cpu_samples"))
        if duration > 10 or samples != 1:
            _add_unavailable(recovered, "service_load", f"{source}:{key}", start, duration, "service samples were aggregated")
            continue
        rss_samples = _finite(value.get("rss_samples"))
        _add_observation(
            recovered, "service_load", f"retired:{key}", start,
            {
                "running": bool(rss_samples),
                "cpu_percent": _finite(value.get("cpu_total_percent")),
                "rss_bytes": _finite(value.get("rss_total_bytes")) if rss_samples else None,
            },
            f"retired:{source}:{start}", 0, f"{source}:service:{key}:{start}",
        )


def _recover_component(
    component: object,
    recovered: _Recovered,
    source: str,
    priority: int,
    row: object | None = None,
) -> bool:
    if not isinstance(component, dict):
        recovered.issues.append(MigrationIssue("unrecoverable_usage", source, "component is not an object"))
        return False
    raw = dict(component)
    if row is not None:
        for index, name in enumerate(("event_id", "direction", "modality", "cache_role", "unit", "timestamp")):
            raw.setdefault(name, row[index])
    if "quantity" not in raw:
        recovered.issues.append(MigrationIssue("unrecoverable_usage", source, "quantity is missing"))
        return False
    event_id = str(raw.get("event_id") or "").strip()
    if not event_id:
        identity_parts = [
            raw.get(name) for name in (
                "provider", "model", "timestamp", "direction", "modality", "cache_role",
                "unit", "quantity", "tmux_key", "agent_thread_id", "root_thread_id",
            )
        ]
        event_id = "retired:" + hashlib.sha256(
            json.dumps(identity_parts, default=str, separators=(",", ":")).encode()
        ).hexdigest()
    event_id = _canonicalize_legacy_identity(
        recovered, event_id, "event", maximum_bytes=identity.MAX_EVENT_ID_BYTES,
    )
    agent_id = _canonicalize_legacy_identity(recovered, str(
        raw.get("agent_id") or raw.get("tmux_key") or raw.get("agent_thread_id")
        or raw.get("root_thread_id") or "unknown"
    ).strip(), "agent")
    payload: dict[str, object] = {
        "quantity": raw.get("quantity", 0),
        "provider": _canonicalize_legacy_identity(
            recovered, str(raw.get("provider") or "unknown"), "provider",
        ),
        "model": _canonicalize_legacy_identity(
            recovered, str(raw.get("model") or "unknown"), "model",
        ),
        "agent_id": agent_id,
        "telemetry_complete": raw.get("telemetry_complete", False),
    }
    optional = {
        "pricing_profile": raw.get("pricing_profile"),
        "service_tier": raw.get("service_tier"),
        "effort": raw.get("effort"),
        "execution_source": raw.get("execution_source") or raw.get("agent_kind"),
        "thread_id": raw.get("thread_id") or raw.get("agent_thread_id") or raw.get("root_thread_id"),
    }
    payload.update({
        key: _canonicalize_legacy_identity(recovered, str(value), key)
        for key, value in optional.items()
        if value is not None and value != ""
    })
    try:
        atom = normalize_usage_atom(UsageAtom(
            event_id,
            str(raw.get("direction") or ""),
            str(raw.get("modality") or ""),
            str(raw.get("cache_role") or ""),
            str(raw.get("unit") or ""),
            raw.get("timestamp", raw.get("sample_time")),
            payload,
        ))
    except UsageValidationError as error:
        recovered.issues.append(MigrationIssue("unrecoverable_usage", source, str(error)))
        return False
    _add_usage(recovered, atom, priority, source)
    return True


def _add_observation(
    recovered: _Recovered,
    family: str,
    source_id: str,
    observed_at: float,
    payload: dict[str, object],
    epoch_id: str,
    owner_generation: int,
    identity_seed: str,
) -> None:
    normalized = validate_payload(family, payload)
    source_id = _canonicalize_legacy_identity(recovered, source_id, "source")
    epoch_id = _canonicalize_legacy_identity(recovered, epoch_id, "epoch")
    event_id = "retired:" + hashlib.sha256(identity_seed.encode()).hexdigest()
    item = Observation(event_id, family, source_id, observed_at, epoch_id, owner_generation, normalized)
    key = (family, source_id, event_id)
    previous = recovered.observations.get(key)
    if previous is not None and previous != item:
        raise MigrationError(f"conflicting retired observation identity: {key}")
    recovered.observations[key] = item
    cadence = _native_cadence(family)
    coverage = CoverageEpoch(family, source_id, epoch_id, observed_at, observed_at + cadence, cadence, owner_generation)
    coverage_key = (family, source_id, epoch_id)
    previous_coverage = recovered.coverage.get(coverage_key)
    if previous_coverage is None:
        recovered.coverage[coverage_key] = coverage
    else:
        recovered.coverage[coverage_key] = CoverageEpoch(
            family,
            source_id,
            epoch_id,
            min(previous_coverage.started_at, coverage.started_at),
            max(previous_coverage.ended_at or coverage.ended_at, coverage.ended_at),
            cadence,
            max(previous_coverage.owner_generation, owner_generation),
        )


def _add_usage(recovered: _Recovered, atom: UsageAtom, priority: int, source: str) -> None:
    key = (atom.event_id, atom.direction, atom.modality, atom.cache_role, atom.unit)
    previous = recovered.usage.get(key)
    if previous is None or priority > previous[0]:
        recovered.usage[key] = (priority, atom)
    elif previous[1] != atom:
        recovered.issues.append(MigrationIssue("duplicate_usage_conflict", source, atom.event_id))


def _add_unavailable(
    recovered: _Recovered,
    family: str,
    source: str,
    start: float,
    duration: float,
    reason: str,
) -> None:
    if family not in FAMILY_BY_NAME:
        recovered.issues.append(MigrationIssue("unsupported_family", family, reason))
        return
    coverage_family = FAMILY_BY_NAME[family].coverage_family
    source_id = "retired-unavailable:" + hashlib.sha256(source.encode()).hexdigest()[:16]
    end = start + duration
    epoch = f"retired:{coverage_family}:{int(start)}:{int(duration)}"
    item = UnavailableSpan(
        coverage_family, source_id, epoch, start, end,
        _native_cadence(coverage_family, fallback=duration), reason, 0,
    )
    recovered.unavailable[(coverage_family, source_id, epoch, start, end)] = item
    recovered.issues.append(MigrationIssue("unavailable_span", source, f"{coverage_family}: {reason}"))


def _native_cadence(family: str, *, fallback: float = 1) -> float:
    spec = FAMILY_BY_NAME.get(family)
    if spec is None or spec.active_cadence_seconds is None:
        return max(1.0, fallback)
    return float(spec.active_cadence_seconds)


def _retain_current_window(recovered: _Recovered, completed_at: float) -> None:
    cutoff = completed_at - RETENTION_SECONDS
    recovered.observations = {
        key: item for key, item in recovered.observations.items()
        if item.observed_at >= cutoff
    }
    recovered.usage = {
        key: item for key, item in recovered.usage.items()
        if item[1].observed_at >= cutoff
    }
    retained_coverage: dict[tuple[str, str, str], CoverageEpoch] = {}
    for key, item in recovered.coverage.items():
        if item.ended_at is not None and item.ended_at <= cutoff:
            continue
        retained_coverage[key] = CoverageEpoch(
            item.family,
            item.source_id,
            item.epoch_id,
            max(item.started_at, cutoff),
            item.ended_at,
            item.native_cadence_seconds,
            item.owner_generation,
        )
    recovered.coverage = retained_coverage
    retained_unavailable: dict[tuple[str, str, str, float, float], UnavailableSpan] = {}
    for item in recovered.unavailable.values():
        if item.ended_at <= cutoff:
            continue
        retained = UnavailableSpan(
            item.family,
            item.source_id,
            item.epoch_id,
            max(item.started_at, cutoff),
            item.ended_at,
            item.native_cadence_seconds,
            item.reason,
            item.owner_generation,
        )
        retained_unavailable[
            (
                retained.family,
                retained.source_id,
                retained.epoch_id,
                retained.started_at,
                retained.ended_at,
            )
        ] = retained
    recovered.unavailable = retained_unavailable


def _validate_materializations(
    snapshot: StoreSnapshot,
    source_generation: int,
    completed_at: float,
) -> None:
    try:
        generation = materializer.build_generation(
            snapshot,
            source_generation=source_generation,
            cache_generation=1,
            generated_at=completed_at,
            observed_until=completed_at,
        )
        if {layer.resolution for layer in generation.layers} != set(materializer.RESOLUTIONS):
            raise MigrationError("shadow materialization did not build all current resolutions")
        for resolution in materializer.RESOLUTIONS:
            layer = materializer.slice_generation(
                generation,
                materializer.LAYER_SECONDS[resolution],
                resolution,
            )
            if layer.resolution != resolution:
                raise MigrationError(f"shadow {resolution}s materialization returned the wrong layer")
    except materializer.MaterializationError as error:
        raise MigrationError(f"shadow materialization failed: {error}") from error


def _timestamp(value: object, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise MigrationError(f"{name} must be a finite non-negative timestamp") from error
    if not math.isfinite(number) or number < 0:
        raise MigrationError(f"{name} must be a finite non-negative timestamp")
    return number


def _json_object(value: object, name: str) -> dict[str, Any]:
    decoded = json.loads(str(value))
    if not isinstance(decoded, dict):
        raise MigrationError(f"{name} must contain an object")
    return decoded


def _finite(value: object) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return number if math.isfinite(number) and number >= 0 else 0.0


def _atom_bytes(atom: UsageAtom) -> bytes:
    return json.dumps({
        "event_id": atom.event_id, "direction": atom.direction, "modality": atom.modality,
        "cache_role": atom.cache_role, "unit": atom.unit, "observed_at": atom.observed_at,
        "payload": dict(atom.payload),
    }, sort_keys=True, separators=(",", ":")).encode()


def _hash_file(digest: Any, name: str, path: Path) -> None:
    digest.update(name.encode())
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)


def _build_retirement_plan(state_dir: Path, legacy: Path) -> _RetirementPlan:
    state_root = state_dir.resolve(strict=False)
    candidates: dict[Path, str] = {}

    def add(path: Path, action: str) -> None:
        if not path.exists() and not path.is_symlink():
            return
        if path.is_symlink() or not path.is_file():
            raise MigrationError(f"retired artifact is not a regular file: {path.name}")
        resolved = path.resolve(strict=True)
        if resolved != state_root and state_root not in resolved.parents:
            raise MigrationError(f"retired artifact escapes the state directory: {path.name}")
        candidates[path] = action

    for suffix in ("", "-wal", "-shm", "-journal"):
        add(Path(f"{legacy}{suffix}"), "delete")
    for name in RETIRED_JSON_FILENAMES[:-1]:
        add(state_dir / name, "delete")
    shared = state_dir / RETIRED_JSON_FILENAMES[-1]
    if shared.is_file():
        value = _read_json_file(shared, "retired shared JSON")
        if "stats_history" in value:
            add(shared, "rewrite_shared")
    services = state_dir / "services"
    if services.is_dir():
        for path in sorted(services.glob("statsd-agent-token-scan-*")):
            name = path.name
            if (
                ".atoms.sqlite3" in name
                or name.endswith(".json")
            ):
                add(path, "delete")
    add(state_dir / WRITER_FENCE_FILENAME, "replace_fence")

    artifacts = []
    for path, action in sorted(candidates.items(), key=lambda item: str(item[0])):
        stat = path.stat()
        artifacts.append(_RetiredArtifact(
            path.relative_to(state_dir).as_posix(),
            action,
            _sha256_file(path),
            stat.st_size,
            stat.st_mode & 0o7777,
            stat.st_mtime_ns,
        ))
    return _RetirementPlan(state_dir, tuple(artifacts))


def _plan_payload(plan: _RetirementPlan) -> dict[str, object]:
    return {
        "format": RETIREMENT_FORMAT,
        "artifacts": [
            {
                "path": item.relative_path,
                "action": item.action,
                "sha256": item.sha256,
                "size": item.size,
                "mode": item.mode,
                "mtime_ns": item.mtime_ns,
            }
            for item in plan.artifacts
        ],
    }


def _prepare_retirement_archive(plan: _RetirementPlan) -> None:
    if plan.archive_path.exists() or plan.journal_path.exists():
        raise MigrationError("an earlier stats retirement transaction was not recovered")
    descriptor, raw_temporary = tempfile.mkstemp(
        prefix=".stats-v5-retirement-", suffix=".tmp", dir=plan.state_dir,
    )
    os.close(descriptor)
    temporary = Path(raw_temporary)
    try:
        with zipfile.ZipFile(
            temporary,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=1,
            allowZip64=True,
        ) as archive:
            archive.writestr(
                RETIREMENT_MANIFEST_MEMBER,
                json.dumps(_plan_payload(plan), sort_keys=True, separators=(",", ":")),
            )
            for item in plan.artifacts:
                archive.write(plan.state_dir / item.relative_path, arcname=item.relative_path)
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary, plan.archive_path)
        plan.archive_path.chmod(0o600)
        _fsync_directory(plan.state_dir)
        archived = _read_retirement_archive(plan.state_dir)
        if archived.artifacts != plan.artifacts:
            raise MigrationError("retirement archive manifest does not match its source plan")
        _write_retirement_journal(plan, "prepared")
    except (OSError, ValueError, zipfile.BadZipFile, MigrationError) as error:
        try:
            temporary.unlink(missing_ok=True)
            if not plan.journal_path.exists():
                plan.archive_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise MigrationError(f"retirement archive could not be prepared: {error}") from error


def _read_retirement_archive(state_dir: Path) -> _RetirementPlan:
    path = state_dir / RETIREMENT_ARCHIVE_FILENAME
    if path.is_symlink() or not path.is_file():
        raise MigrationError("retirement archive is missing or unsafe")
    try:
        with zipfile.ZipFile(path, "r") as archive:
            manifest_info = archive.getinfo(RETIREMENT_MANIFEST_MEMBER)
            if manifest_info.file_size > 1_000_000:
                raise MigrationError("retirement archive manifest is too large")
            payload = json.loads(archive.read(manifest_info))
            plan = _plan_from_payload(state_dir, payload)
            expected = {RETIREMENT_MANIFEST_MEMBER, *(item.relative_path for item in plan.artifacts)}
            names = archive.namelist()
            if len(names) != len(expected) or set(names) != expected:
                raise MigrationError("retirement archive has unexpected members")
            for item in plan.artifacts:
                info = archive.getinfo(item.relative_path)
                if info.file_size != item.size:
                    raise MigrationError("retirement archive member size does not reconcile")
                digest = hashlib.sha256()
                size = 0
                with archive.open(info, "r") as source:
                    while chunk := source.read(1024 * 1024):
                        digest.update(chunk)
                        size += len(chunk)
                if size != item.size or digest.hexdigest() != item.sha256:
                    raise MigrationError("retirement archive member digest does not reconcile")
            return plan
    except (KeyError, OSError, json.JSONDecodeError, zipfile.BadZipFile) as error:
        raise MigrationError(f"retirement archive cannot be read: {error}") from error


def _plan_from_payload(state_dir: Path, payload: object) -> _RetirementPlan:
    if not isinstance(payload, dict) or payload.get("format") != RETIREMENT_FORMAT:
        raise MigrationError("retirement manifest format is unsupported")
    raw_artifacts = payload.get("artifacts")
    if not isinstance(raw_artifacts, list) or len(raw_artifacts) > 10_000:
        raise MigrationError("retirement manifest artifacts are malformed")
    artifacts = []
    seen = set()
    for raw in raw_artifacts:
        if not isinstance(raw, dict) or set(raw) != {
            "path", "action", "sha256", "size", "mode", "mtime_ns",
        }:
            raise MigrationError("retirement artifact is malformed")
        relative = raw["path"]
        action = raw["action"]
        digest = raw["sha256"]
        if (
            not isinstance(relative, str)
            or not relative
            or len(relative.encode("utf-8")) > 1_024
            or Path(relative).is_absolute()
            or ".." in Path(relative).parts
            or relative in seen
        ):
            raise MigrationError("retirement artifact path is unsafe")
        if action not in {"delete", "rewrite_shared", "replace_fence"}:
            raise MigrationError("retirement artifact action is unsupported")
        if not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise MigrationError("retirement artifact digest is malformed")
        size = _nonnegative_count(raw["size"], "retirement artifact size")
        mode = _nonnegative_count(raw["mode"], "retirement artifact mode")
        mtime_ns = _nonnegative_count(raw["mtime_ns"], "retirement artifact mtime")
        if mode > 0o7777:
            raise MigrationError("retirement artifact mode is malformed")
        seen.add(relative)
        artifacts.append(_RetiredArtifact(relative, action, digest, size, mode, mtime_ns))
    return _RetirementPlan(state_dir, tuple(artifacts))


def _write_retirement_journal(
    plan: _RetirementPlan,
    state: str,
    *,
    failure: str = "",
) -> None:
    if state not in {"prepared", "retiring", "activating", "activated", "rolled_back", "restore_failed"}:
        raise MigrationError("retirement journal state is invalid")
    payload = {
        "format": RETIREMENT_FORMAT,
        "archive": RETIREMENT_ARCHIVE_FILENAME,
        "state": state,
        "failure": failure[:64],
    }
    atomic_write_text(
        plan.journal_path,
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        mode=0o600,
    )
    _fsync_directory(plan.state_dir)


def _read_retirement_journal(path: Path) -> str:
    value = _read_json_file(path, "retirement journal")
    if (
        set(value) != {"format", "archive", "state", "failure"}
        or value["format"] != RETIREMENT_FORMAT
        or value["archive"] != RETIREMENT_ARCHIVE_FILENAME
        or value["state"] not in {
            "prepared", "retiring", "activating", "activated", "rolled_back", "restore_failed",
        }
        or not isinstance(value["failure"], str)
        or len(value["failure"]) > 64
    ):
        raise MigrationError("retirement journal is malformed")
    return str(value["state"])


def _retire_legacy_sources(plan: _RetirementPlan) -> None:
    _write_retirement_journal(plan, "retiring")
    for item in plan.artifacts:
        path = plan.state_dir / item.relative_path
        _verify_retired_artifact(path, item)
    touched = {plan.state_dir}
    for item in plan.artifacts:
        path = plan.state_dir / item.relative_path
        touched.add(path.parent)
        if item.action == "delete":
            path.unlink()
        elif item.action == "rewrite_shared":
            with file_lock(path, dir_mode=0o700):
                _verify_retired_artifact(path, item)
                value = _read_json_file(path, "retired shared JSON")
                if "stats_history" not in value:
                    raise MigrationError("retired shared JSON changed before retirement")
                del value["stats_history"]
                atomic_write_text(
                    path,
                    json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
                    mode=item.mode,
                )
    for directory in sorted(touched, key=str):
        _fsync_directory(directory)


def _verify_retired_artifact(path: Path, item: _RetiredArtifact) -> None:
    if path.is_symlink() or not path.is_file():
        raise MigrationError(f"retired artifact disappeared or changed type: {item.relative_path}")
    stat = path.stat()
    if (
        stat.st_size != item.size
        or stat.st_mtime_ns != item.mtime_ns
        or (stat.st_mode & 0o7777) != item.mode
        or _sha256_file(path) != item.sha256
    ):
        raise MigrationError(f"retired artifact changed during migration: {item.relative_path}")


def _retired_state_matches(plan: _RetirementPlan) -> bool:
    for item in plan.artifacts:
        path = plan.state_dir / item.relative_path
        if item.action == "delete" and (path.exists() or path.is_symlink()):
            return False
        if item.action == "rewrite_shared":
            if path.is_symlink() or not path.is_file():
                return False
            try:
                value = _read_json_file(path, "retired shared JSON")
            except MigrationError:
                return False
            if "stats_history" in value:
                return False
    return True


def _restore_retirement_archive(plan: _RetirementPlan) -> None:
    archived = _read_retirement_archive(plan.state_dir)
    if archived.artifacts != plan.artifacts:
        raise MigrationError("retirement archive changed before rollback")
    try:
        with zipfile.ZipFile(plan.archive_path, "r") as archive:
            for item in plan.artifacts:
                target = plan.state_dir / item.relative_path
                target.parent.mkdir(parents=True, exist_ok=True)
                if item.action == "rewrite_shared":
                    _restore_shared_history(archive, item, target)
                    continue
                temporary = target.with_name(
                    f".{target.name}.{os.getpid()}.{time.time_ns()}.restore"
                )
                descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, item.mode)
                try:
                    with os.fdopen(descriptor, "wb") as output, archive.open(item.relative_path, "r") as source:
                        shutil.copyfileobj(source, output, length=1024 * 1024)
                        output.flush()
                        os.fsync(output.fileno())
                    os.replace(temporary, target)
                    target.chmod(item.mode)
                    os.utime(target, ns=(item.mtime_ns, item.mtime_ns))
                finally:
                    temporary.unlink(missing_ok=True)
    except (OSError, zipfile.BadZipFile) as error:
        raise MigrationError(f"retired sources could not be restored: {error}") from error
    fence_relative = Path(WRITER_FENCE_FILENAME).as_posix()
    if all(item.relative_path != fence_relative for item in plan.artifacts):
        fence = plan.state_dir / WRITER_FENCE_FILENAME
        if fence.exists():
            value = _read_json_file(fence, "current writer fence")
            if value.get("schema_version") != SCHEMA_VERSION:
                raise MigrationError("refusing to remove an unrecognized writer fence during rollback")
            fence.unlink()
    directories = {plan.state_dir}
    directories.update((plan.state_dir / item.relative_path).parent for item in plan.artifacts)
    for directory in directories:
        _fsync_directory(directory)


def _restore_shared_history(
    archive: zipfile.ZipFile,
    item: _RetiredArtifact,
    target: Path,
) -> None:
    try:
        original_bytes = archive.read(item.relative_path)
        original = json.loads(original_bytes)
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError, zipfile.BadZipFile) as error:
        raise MigrationError(f"retired shared JSON backup is invalid: {error}") from error
    if not isinstance(original, dict) or "stats_history" not in original:
        raise MigrationError("retired shared JSON backup has no stats history")
    with file_lock(target, dir_mode=0o700):
        exists = target.is_file()
        current = _read_json_file(target, "current shared JSON") if exists else {}
        original_without_history = dict(original)
        del original_without_history["stats_history"]
        if not exists or current == original_without_history:
            payload = original_bytes
            restore_mtime = True
        else:
            current["stats_history"] = original["stats_history"]
            payload = (
                json.dumps(current, sort_keys=True, separators=(",", ":")) + "\n"
            ).encode("utf-8")
            restore_mtime = False
        _atomic_write_bytes(target, payload, item.mode)
        if restore_mtime:
            os.utime(target, ns=(item.mtime_ns, item.mtime_ns))


def _deactivate_current(target: Path) -> Path | None:
    if not target.exists():
        return None
    stamp = f"{time.time_ns()}-{os.getpid()}"
    diagnostic = target.with_name(f"stats-v{SCHEMA_VERSION}.failed-{stamp}.sqlite3")
    os.replace(target, diagnostic)
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{target}{suffix}")
        if sidecar.exists():
            os.replace(sidecar, Path(f"{diagnostic}{suffix}"))
    _fsync_directory(target.parent)
    return diagnostic


def _rollback_retirement(
    plan: _RetirementPlan,
    target: Path,
    error: object,
) -> None:
    try:
        _deactivate_current(target)
        _restore_retirement_archive(plan)
        _write_retirement_journal(plan, "rolled_back", failure=type(error).__name__)
    except (OSError, MigrationError, zipfile.BadZipFile) as restore_error:
        try:
            _write_retirement_journal(plan, "restore_failed", failure=type(restore_error).__name__)
        except (OSError, MigrationError):
            pass
        raise MigrationError(f"migration rollback failed: {restore_error}") from restore_error


def _recover_interrupted_retirement(
    state_dir: Path,
    target: Path,
) -> MigrationReport | None:
    archive_path = state_dir / RETIREMENT_ARCHIVE_FILENAME
    journal_path = state_dir / RETIREMENT_JOURNAL_FILENAME
    if not archive_path.exists() and not journal_path.exists():
        return None
    if journal_path.is_symlink() or archive_path.is_symlink():
        raise MigrationError("retirement recovery files cannot be symbolic links")
    state = _read_retirement_journal(journal_path) if journal_path.exists() else "orphaned"
    if not archive_path.exists():
        if target.exists() and state == "activated":
            report = _active_report(target)
            journal_path.unlink()
            _fsync_directory(state_dir)
            return report
        raise MigrationError("retirement archive is missing before rollback completed")
    plan = _read_retirement_archive(state_dir)
    if target.exists():
        try:
            # A crash can occur after the atomic replace but before the first
            # current writer reopen/fence publication. Complete that exact
            # step before declaring the interrupted activation usable.
            with Store.open(target):
                pass
            report = _active_report(target)
        except (OSError, sqlite3.Error, StatsCurrentError, MigrationError) as error:
            _rollback_retirement(plan, target, error)
            _remove_retirement_journal(plan, missing_ok=True)
            _discard_retirement_archive(plan)
            return None
        else:
            try:
                if not _retired_state_matches(plan):
                    _retire_legacy_sources(plan)
                _remove_retirement_journal(plan, missing_ok=True)
                _discard_retirement_archive(plan)
            except (OSError, MigrationError, zipfile.BadZipFile) as error:
                _rollback_retirement(plan, target, error)
                raise MigrationError("interrupted retirement could not finish") from error
            return report
    else:
        _restore_retirement_archive(plan)
        _remove_retirement_journal(plan, missing_ok=True)
        _discard_retirement_archive(plan)
    return None


def _retire_sources_beside_existing_current(
    state_dir: Path,
    legacy: Path,
    target: Path,
    report: MigrationReport,
) -> MigrationReport:
    plan = _build_retirement_plan(state_dir, legacy)
    if not any(item.action != "replace_fence" for item in plan.artifacts):
        return report
    _prepare_retirement_archive(plan)
    try:
        _retire_legacy_sources(plan)
        with Store.open(target):
            pass
        _active_report(target)
        _write_retirement_journal(plan, "activated")
        _remove_retirement_journal(plan)
        _discard_retirement_archive(plan)
    except (OSError, sqlite3.Error, StatsCurrentError, MigrationError, zipfile.BadZipFile) as error:
        _rollback_retirement(plan, target, error)
        raise MigrationError(f"existing current retirement failed: {type(error).__name__}") from error
    return report


def _discard_retirement_archive(plan: _RetirementPlan) -> None:
    plan.archive_path.unlink(missing_ok=True)


def _remove_retirement_journal(plan: _RetirementPlan, *, missing_ok: bool = False) -> None:
    plan.journal_path.unlink(missing_ok=missing_ok)
    _fsync_directory(plan.state_dir)


def _read_json_file(path: Path, name: str) -> dict[str, Any]:
    try:
        value = read_json_file(path, {}, exceptions=())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MigrationError(f"{name} cannot be read: {error}") from error
    if not isinstance(value, dict):
        raise MigrationError(f"{name} must contain an object")
    return value


def _atomic_write_bytes(path: Path, payload: bytes, mode: int) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.restore")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        path.chmod(mode)
    finally:
        temporary.unlink(missing_ok=True)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _compact_database(path: Path) -> None:
    try:
        connection = sqlite3.connect(path)
        connection.execute("VACUUM")
        connection.execute("PRAGMA optimize")
        if int(connection.execute("PRAGMA freelist_count").fetchone()[0]) != 0:
            raise MigrationError("compacted shadow database still has free pages")
    except sqlite3.Error as error:
        raise MigrationError(f"shadow database could not be compacted: {error}") from error
    finally:
        if "connection" in locals():
            connection.close()


def _validate_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise MigrationError("shadow database integrity check failed")
        if int(connection.execute("PRAGMA application_id").fetchone()[0]) != APPLICATION_ID:
            raise MigrationError("shadow database application id is wrong")
        if int(connection.execute("PRAGMA user_version").fetchone()[0]) != SCHEMA_VERSION:
            raise MigrationError("shadow database schema version is wrong")
    finally:
        connection.close()


def _activate_database(shadow: Path, target: Path) -> None:
    # os.replace swaps only the main file. SQLite removes a -wal/-shm pair on the
    # clean close of the last connection, so a killed writer strands them -- and a
    # stranded sidecar belongs to the database that just went away, not to the one
    # being activated. Replaying it into the new file is what produces "database
    # disk image is malformed" on the next start. Set any orphan aside first.
    _quarantine_orphaned_sidecars(target)
    try:
        os.replace(shadow, target)
    except OSError as error:
        raise MigrationError(f"current database activation failed: {error}") from error


def _quarantine_orphaned_sidecars(target: Path) -> None:
    """Move aside -wal/-shm/-journal files left beside an absent current database."""

    if target.exists():
        return
    quarantine = _free_quarantine_target(target, "orphaned")
    for suffix in ("-wal", "-shm", "-journal"):
        candidate = Path(f"{target}{suffix}")
        if not candidate.exists() and not candidate.is_symlink():
            continue
        if candidate.is_symlink() or not candidate.is_file():
            raise MigrationError(
                f"orphaned current database artifact is not a regular file: {candidate.name}"
            )
        os.replace(candidate, Path(f"{quarantine}{suffix}"))
    _fsync_directory(target.parent)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class V8MigrationFault(MigrationError):
    """A deliberately injected interruption at a named migration boundary."""


def _v8_fault(fault_hook: Any, boundary: str) -> None:
    """Give a test one exact place to interrupt, and refuse an unknown boundary name.

    A typo'd boundary would otherwise never fire and the test would pass by never testing
    anything, which is the failure mode this whole slice exists to rule out.
    """
    if boundary not in V8_MIGRATION_BOUNDARIES:
        raise MigrationError(f"unknown migration boundary {boundary!r}")
    if fault_hook is not None:
        fault_hook(boundary)


def _validate_current_v7_database(path: Path) -> None:
    """Validate the exact frozen v7 shape before any v8 shadow is created.

    Fail closed on anything that is not exactly v7: a v8 source is already current, a future
    version is unreadable by this build, and a missing or malformed fact table means the copy
    below would silently produce a v8 store with less history than its source.
    """
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(f"file:{quote(str(path))}?mode=ro", uri=True)
        if int(connection.execute("PRAGMA application_id").fetchone()[0]) != APPLICATION_ID:
            raise MigrationError("schema-7 source has the wrong application id")
        found = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if found != V7_SCHEMA_VERSION:
            raise MigrationError(
                f"schema-7 source has user_version {found}, expected {V7_SCHEMA_VERSION}"
            )
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        missing = set(V7_FACT_TABLE_COLUMNS) - tables
        if missing:
            raise MigrationError(f"schema-7 source is missing fact tables {sorted(missing)}")
        if "schema_meta" not in tables:
            raise MigrationError("schema-7 source is missing schema_meta")
        for table, expected in V7_FACT_TABLE_COLUMNS.items():
            columns = tuple(
                str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")
            )
            if columns != expected:
                raise MigrationError(f"schema-7 source table {table} has unexpected columns")
    except sqlite3.Error as error:
        raise MigrationError(f"schema-7 source is unreadable: {type(error).__name__}") from error
    finally:
        if connection is not None:
            connection.close()


def _copy_v7_fact_table(
    connection: sqlite3.Connection,
    table: str,
    columns: tuple[str, ...],
    fault_hook: Any,
) -> tuple[int, int]:
    """Copy one fact table in bounded keyed chunks, returning (rows, decoded bytes).

    Every fact table is WITHOUT ROWID with a composite primary key, so pagination is by that key
    rather than by OFFSET: an OFFSET scan re-walks the prefix on every chunk and turns a linear
    copy into a quadratic one. `INSERT OR IGNORE` makes a resumed or repeated chunk idempotent,
    which is what lets an interrupted run be restarted without duplicating or skipping a row.
    """
    key_columns = tuple(
        str(row[1])
        for row in connection.execute(f"PRAGMA source_v7.table_info({table})")
        if int(row[5]) > 0
    )
    if not key_columns:
        raise MigrationError(f"schema-7 source table {table} has no primary key to page by")
    column_list = ", ".join(columns)
    key_list = ", ".join(key_columns)
    placeholders = ", ".join("?" for _ in key_columns)
    rows_copied = 0
    bytes_copied = 0
    cursor: tuple[Any, ...] | None = None
    while True:
        if cursor is None:
            query = (
                f"SELECT {column_list} FROM source_v7.{table} "
                f"ORDER BY {key_list} LIMIT ?"
            )
            parameters: tuple[Any, ...] = (V8_MIGRATION_ROW_CHUNK,)
        else:
            query = (
                f"SELECT {column_list} FROM source_v7.{table} "
                f"WHERE ({key_list}) > ({placeholders}) ORDER BY {key_list} LIMIT ?"
            )
            parameters = (*cursor, V8_MIGRATION_ROW_CHUNK)
        chunk = connection.execute(query, parameters).fetchmany(V8_MIGRATION_ROW_CHUNK)
        if not chunk:
            break
        key_indexes = tuple(columns.index(name) for name in key_columns)
        connection.executemany(
            f"INSERT OR IGNORE INTO {table}({column_list}) "
            f"VALUES({', '.join('?' for _ in columns)})",
            chunk,
        )
        rows_copied += len(chunk)
        bytes_copied += sum(
            len(value) if isinstance(value, (str, bytes)) else 8
            for row in chunk
            for value in row
        )
        cursor = tuple(chunk[-1][index] for index in key_indexes)
        # Deterministically at every chunk boundary. An arithmetic trigger here -- fire when the
        # running byte total crosses a threshold -- reads as more precise and is worse: whether it
        # ever fires depends on fixture size, so a boundary test could pass by never interrupting.
        _v8_fault(fault_hook, "during_population")
        if len(chunk) < V8_MIGRATION_ROW_CHUNK:
            break
    if bytes_copied > V8_MIGRATION_BYTE_CHUNK * max(1, rows_copied // V8_MIGRATION_ROW_CHUNK + 1):
        raise MigrationError(
            f"schema-7 table {table} exceeded its decoded-byte budget at {bytes_copied} bytes"
        )
    return rows_copied, bytes_copied


def _record_v7_ring_invalidations(connection: sqlite3.Connection, now: float) -> int:
    """Refuse to carry v7 ring payloads across, and record what must be rebuilt instead.

    A v7 slot carries no `payload_version`, so its `bucket_json` cannot be PROVEN equivalent to
    what schema 8's serving path decodes. Copying it would move plausible bytes into a store that
    now claims to validate them -- the exact false-confidence the payload version was added to end.

    Every populated v7 slot therefore becomes an unapplied `ring_invalidations` row bound to its
    exact resolution, bucket_start and source generation, and the v8 ring starts empty. No history
    is lost: the FACTS are preserved above, so replay rebuilds each bucket from data that is still
    authoritative. An empty ring reads as gaps, which is honest; a mis-decoded ring would not be.
    """
    rows = connection.execute(
        "SELECT resolution_seconds, bucket_start, source_generation "
        "FROM source_v7.aggregate_ring_slots "
        "WHERE bucket_start IS NOT NULL AND bucket_json IS NOT NULL"
    )
    recorded = 0
    while True:
        chunk = rows.fetchmany(V8_MIGRATION_ROW_CHUNK)
        if not chunk:
            break
        connection.executemany(
            "INSERT OR IGNORE INTO ring_invalidations("
            "resolution_seconds, bucket_start, source_generation, reason, created_at, applied_at) "
            "VALUES(?, ?, ?, 'v7_payload_unversioned', ?, NULL)",
            [(int(row[0]), int(row[1]), int(row[2]), now) for row in chunk],
        )
        recorded += len(chunk)
    return recorded


def migrate_current_v7_database(
    state_dir: Path,
    target: Path,
    source: Path,
    *,
    fault_hook: Any = None,
    now: float | None = None,
) -> MigrationReport:
    """Copy a schema-7 current store into a schema-8 store, bounded and restartable.

    The shadow is built in a temporary directory beside the target and activated by one atomic
    rename, so an interruption leaves either the untouched v7 source plus a discarded partial
    shadow, or a fully validated v8. There is no window in which a mixed format is reachable: the
    target name `stats-v8.sqlite3` only ever appears as the finished file.

    Restart is by RESTART, not by resume-from-offset. The partial shadow lives in a temporary
    directory that is removed on the way out, so a repeated run starts from a clean shadow and
    `INSERT OR IGNORE` makes every chunk idempotent regardless. That is deliberately the boring
    choice: a resume cursor would have to be durable and correct across exactly the crash points
    this function is trying to survive, and getting it wrong skips rows silently.

    The v7 source is opened READ-ONLY through a copy and is never written, so the running v7 build
    keeps its file byte-identical -- that is the whole rollback boundary.
    """
    stamp = time.time() if now is None else float(now)
    _v8_fault(fault_hook, "before_shadow_creation")
    with tempfile.TemporaryDirectory(prefix=".stats-v8-migration-", dir=target.parent) as temporary:
        work = Path(temporary)
        source_digest = hashlib.sha256()
        copied_source = _copy_database(source, work / "source", source_digest)
        _validate_current_v7_database(copied_source)
        shadow = work / DATABASE_FILENAME
        copied_rows: dict[str, int] = {}
        copied_bytes: dict[str, int] = {}
        with Store.open(shadow) as store:
            _v8_fault(fault_hook, "after_shadow_creation")
            connection = store._connection()
            connection.execute("ATTACH DATABASE ? AS source_v7", (str(copied_source),))
            try:
                connection.execute("BEGIN IMMEDIATE")
                for table, columns in V7_FACT_TABLE_COLUMNS.items():
                    rows, size = _copy_v7_fact_table(connection, table, columns, fault_hook)
                    copied_rows[table] = rows
                    copied_bytes[table] = size
                invalidated = _record_v7_ring_invalidations(connection, stamp)
                source_generation = int(connection.execute(
                    "SELECT source_generation FROM source_v7.schema_meta WHERE singleton = 1"
                ).fetchone()[0])
                connection.execute(
                    "UPDATE schema_meta SET minimum_writer_protocol = ?, minimum_writer_build = ?, "
                    "source_generation = ?, last_vacuumed_at = 0 WHERE singleton = 1",
                    (MIN_WRITER_PROTOCOL, MIN_WRITER_BUILD, source_generation),
                )
                # The publication LINEAGE is preserved even though no payload is: a later ring
                # generation must not collide with one this store already issued.
                publication = connection.execute(
                    "SELECT ring_generation, published_at FROM source_v7.aggregate_publication "
                    "WHERE singleton = 1"
                ).fetchone()
                if publication is not None:
                    connection.execute(
                        "UPDATE aggregate_publication SET ring_generation = ?, "
                        "source_generation = 0, published_at = ? WHERE singleton = 1",
                        (int(publication[0]), float(publication[1])),
                    )
                # The cursor starts at zero on purpose. Nothing has been folded into THIS ring, so
                # claiming otherwise would let replay skip exactly the buckets it must rebuild.
                connection.execute(
                    "UPDATE ring_replay_cursor SET folded_through_observed_at = 0, "
                    "folded_source_generation = 0, updated_at = ?",
                    (stamp,),
                )
                connection.execute("COMMIT")
            except BaseException:
                # ANY interruption, not only `sqlite3.Error`. A crash mid-copy is the case this
                # function exists to survive, and it does not arrive as a SQLite error: it arrives
                # as whatever the caller raised. Narrowing this left the transaction open, so the
                # DETACH below failed with "database is locked" and replaced the real cause with a
                # misleading one.
                connection.execute("ROLLBACK")
                raise
            finally:
                connection.execute("DETACH DATABASE source_v7")
            _v8_fault(fault_hook, "before_validation")
            Store._validate_schema_shape(connection)
        _validate_database(shadow)
        _compact_database(shadow)
        _validate_database(shadow)
        _v8_fault(fault_hook, "before_cutover")
        if target.exists():
            raise MigrationError("current database appeared during schema-7 migration")
        _activate_database(shadow, target)
        _fsync_directory(target.parent)
        with Store.open(target):
            pass
        _v8_fault(fault_hook, "after_commit")
    # Built from the counts this migration STREAMED, not by re-reading the finished store.
    #
    # `_active_report()` was the obvious call here and it is measurably the wrong one: on a
    # representative 415 MiB v7 store it raised this process's peak RSS from 44.9 MiB to 1279.0
    # MiB, +1234 MiB in one call, because it goes through `read_snapshot()` and materializes every
    # decoded observation to count them. Migrating a store is exactly when memory is scarcest, and
    # the copier already counted every row it moved, so re-deriving those counts by decoding the
    # whole store is both slower and the precise defect class this schema exists to bound.
    return MigrationReport(
        target,
        source_digest.hexdigest(),
        copied_rows.get("observations", 0),
        copied_rows.get("coverage_epochs", 0),
        copied_rows.get("usage_atoms", 0),
        copied_rows.get("unavailable_spans", 0),
        (
            MigrationIssue("current_schema", source.name, str(V7_SCHEMA_VERSION)),
            MigrationIssue("ring_payload_rebuild", source.name, str(invalidated)),
        ),
        1,
        False,
    )
