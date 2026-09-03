# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Small current-schema-only SQLite store for original YO!stats facts."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import time
import zlib
from contextlib import contextmanager
from dataclasses import dataclass
from dataclasses import replace
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
# The ring schema added a per-resolution replay cursor, an invalidation
# ledger, and a versioned slot payload. It is a SIDE-BY-SIDE format, not an in-place upgrade.
# DATABASE_FILENAME embeds the version, so each schema build addresses its own file and cannot
# half-share a store with an older build. Migration is an explicit, bounded copy.
SCHEMA_VERSION = common.STATS_SCHEMA_VERSION
MIN_WRITER_PROTOCOL = 25
# Build 4 moved recurring CPU/GPU host sampling into statsd. Build 5 added the
# strict process-memory payload. Build 6 makes the same census the sole owner of
# grouped CPU and RSS. Build 7 stores large persisted-ring JSON as bounded zlib
# blobs, so an older reader must yield before it can reinterpret those slots.
MIN_WRITER_BUILD = 8
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
# Rows per pass for the retired-invalidation delete, and the ONLY delete here that is bounded.
# Every other retention delete is unbounded because cadence already bounds it: the cutoff sweep
# reruns each PRUNE_CHECK_SECONDS, so a healthy store never holds more than a minute of expired
# facts. `ring_invalidations` had no delete at all, so it starts with a real backlog -- measured
# 435,835 retired rows past the cutoff on the live store, which one unbounded statement cleared in
# 0.891 s of write-lock hold. This delete runs inside the service work lock, and the gate fails a
# YO!stats stream generation stall at 3 s, so a one-time 0.9 s hold is not a defensible bound. At
# this limit the same backlog measured 0.045 s per pass and drains over roughly 88 sweeps.
RETIRED_INVALIDATION_PRUNE_LIMIT = 5_000
WAL_AUTOCHECKPOINT_PAGES = 1000
# Automatic checkpoints recycle logical frames but do not necessarily shrink
# the file. This connection policy bounds the retained allocation after a reset;
# Store.vacuum() separately truncates the rewrite-sized allocation immediately.
WAL_ALLOCATION_CEILING_BYTES = 8 * 1024 * 1024
DATABASE_FILENAME = common.STATS_DATABASE_FILENAME
# Sidecar beside the database, like WRITER_FENCE_FILENAME. The nightly prune
# schedule needs the last successful prune to survive a restart, and schema_meta
# cannot grow a column without a SCHEMA_VERSION bump that would strand the
# operator's existing history.
# VERSIONED, like DATABASE_FILENAME and SOCKET_FILENAME. Prune state is mutable companion state
# belonging to one database, so an unversioned name would let one schema rewrite another's schedule.
PRUNE_STATE_FILENAME = f"stats-prune-v{SCHEMA_VERSION}.json"
# The compaction-benefit baseline, in a sidecar for exactly the reason stated above: schema_meta
# cannot grow a column without a SCHEMA_VERSION bump, and the format is side-by-side, so a
# bump would strand the operator's entire existing history to persist one float.
#
# The sidecar cannot share the marker's transaction, so it carries the marker VALUE instead and is
# only believed when the two agree. A crash between the marker and this file leaves a sidecar
# naming the PREVIOUS vacuum, which no longer matches `schema_meta.last_vacuumed_at`, so the
# baseline reads as unknown rather than as a stale number quietly presented as current. Detecting
# the disagreement is what atomicity would have bought; hiding it is what a bare float would have
# cost.
VACUUM_BASELINE_FILENAME = f"stats-vacuum-v{SCHEMA_VERSION}.json"


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
# VERSIONED for the same reason, and this one is load-bearing for rollback. The fence is a
# deliberate cross-BUILD guard: a writer whose schema is older than the fence refuses to open. The
# fence is versioned with the database so side-by-side schema builds do not rewrite each other's
# compatibility state.
WRITER_FENCE_FILENAME = f"stats-writer-compat-v{SCHEMA_VERSION}.json"
MAX_DIRTY_INTERVALS = 32
MAX_BROWSER_FAILURE_FINGERPRINTS = 128
MAX_RING_BUCKET_BYTES = 256 * 1024
_RING_BUCKET_ZLIB_MAGIC = b"YRB1\0"
_RING_BUCKET_ZLIB_LEVEL = 1

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
    {
        "aggregate_publication",
        "aggregate_rings",
        "aggregate_ring_slots",
        "ring_replay_cursor",
        "ring_invalidations",
    }
)
# The fixed-row triggers guard the three tables whose row set is pre-allocated and immutable. The
# two schema-8 tables are deliberately NOT in that set: the cursor has one row per resolution but
# is UPDATE-heavy, so a reject trigger would refuse its normal writes, and the ledger is
# append-and-retire by nature, so pinning its rows would defeat its purpose. The cursor's row set
# is still fixed, and `_require_exact_fixed_rows` asserts it during validation instead - a trigger
# is the wrong instrument for a table that must accept updates.
_RING_FIXED_ROW_TABLES = frozenset(
    {"aggregate_publication", "aggregate_rings", "aggregate_ring_slots"}
)
# The payload schema `bucket_json` is written with. The current format stops accepting shape-only ring data:
# serving decodes named fields out of this blob, so a blob written by a build with a different
# payload contract must be refused as data rather than mis-decoded into a plausible chart.
RING_PAYLOAD_VERSION = 2
_RING_COLUMNS = {
    "aggregate_publication": (
        "singleton", "ring_generation", "source_generation", "published_at",
    ),
    "aggregate_rings": (
        "resolution_seconds", "slot_count", "newest_bucket_start",
    ),
    "aggregate_ring_slots": (
        "resolution_seconds", "slot_index", "bucket_start", "bucket_json", "complete",
        "source_generation", "ring_generation", "published_at", "payload_version",
    ),
    # One row per resolution: how far replay has folded, and the store generation it folded at.
    # `folded_through_observed_at` is the durable answer to "which facts are already in the ring",
    # which nothing could answer before: `newest_bucket_start` is a head with no tail, and
    # `source_generation` is a change counter with no time semantics.
    "ring_replay_cursor": (
        "resolution_seconds", "folded_through_observed_at", "folded_source_generation", "updated_at",
    ),
    # Every published bucket a later mutation contradicted, bound to the exact bucket, resolution
    # and store generation that invalidated it. A usage tombstone hard-DELETEs its atoms and prune
    # removes facts outright; without this row the affected slot stays published and permanently
    # over-counted, because the materializer only republishes cells it independently knows are
    # dirty and cannot know about facts that no longer exist.
    "ring_invalidations": (
        "resolution_seconds", "bucket_start", "source_generation", "reason", "created_at",
        "applied_at",
    ),
}
_RING_TRIGGER_NAMES = frozenset(
    f"{table}_reject_{operation}"
    for table in _RING_FIXED_ROW_TABLES
    for operation in ("insert", "delete")
)


class StatsCurrentError(RuntimeError):
    """Base error for a current YO!stats storage failure."""


class SchemaMismatchError(StatsCurrentError):
    """The database is not the exact schema this store understands."""


# The four bounds an exact rebuild runs under. Every number is a measurement, not a guess. The
# long-form derivations live in `docs/specs/STATS_BOUNDED_REBUILD.md`, which arrives with the
# process-binary-cache work and is absent while this branch stands alone -- the arithmetic below is
# the whole story until then. Two of these end a BATCH and two of them end the REBUILD, which is
# the distinction that keeps a bound from becoming a truncation: a full batch is a normal event,
# an over-budget process is not.
#
# 32 MiB is past the knee of the measured chunk-size sweep (397 -> 32.7 -> 28.1 -> 27.2 MiB peak at
# 600k / 10k / 2.5k / 1k rows) and stays smaller than the 53 MiB retained generation, so a batch can
# never dominate the thing it is building.
REBUILD_BATCH_MAX_DECODED_BYTES = 32 * 1024 * 1024
# 32 MiB / 1.46 KiB, the measured cost of one decoded observation (880.32 MiB for 617,243 of them).
# A secondary guard so one pathologically wide payload cannot overshoot the byte bound between
# checks; whichever bound trips first ends the batch.
REBUILD_BATCH_MAX_ROWS = 22_000
# How much a rebuild may GROW the process, measured from the value read when the rebuild starts.
# 106 MiB retained generation with headroom + 32 MiB live batch + 24 MiB encode buffer = 162 MiB,
# and no term for the interpreter or for anything the process was already holding -- growth is the
# only part a rebuild is responsible for.
#
# The constant is 192 MiB, so 30 MiB of it is headroom above the three growth terms, kept
# deliberately rather than derived. It is NOT an interpreter term; the comment above means what it
# says. Tightening the value to 162 MiB is a real option and a real behaviour change -- it can
# refuse a rebuild that 192 MiB admits -- so it is a decision to take on its own, not a tidy-up to
# fold into a release subject.
#
# This was an ABSOLUTE process bound and that made it non-functional. `RssAnon + VmSwap` on the live
# daemon measured 623,244 kB + 942,440 kB = 1,529 MiB, permanently past a 192 MiB ceiling, so an
# absolute check refused every rebuild from the first one after start. It surfaced in a gate worker
# at 300 MiB holding a 200-observation fixture, which is the same defect in a smaller process.
# Still checked against RssAnon + VmSwap, never RSS -- see `_rebuild_memory_bytes`.
REBUILD_MAX_MEMORY_BYTES = 192 * 1024 * 1024
# A cold build measured 26.80 s at 1.19 M observations on a loaded host, and 2x cardinality measured
# 2.054x readiness, so ~55 s at 2x. 120 s fires on genuine pathology and never on a slow host.
REBUILD_MAX_SECONDS = 120.0
# How many raw rows are pulled from SQLite at a time. Smaller than a batch on purpose: the raw
# tuples for one fetch are decoded and dropped before the next fetch, so raw rows and decoded facts
# for a whole batch are never both live. See `pinned_snapshot_batches`.
_REBUILD_FETCH_ROWS = 2_000


class RebuildBoundExceeded(StatsCurrentError):
    """A rebuild exceeded one of its bounds and abandoned the candidate.

    Carries a machine-readable `reason` so a caller records WHICH bound stopped it rather than a
    formatted sentence. The rebuild is abandoned, not truncated: a half-built generation served as
    if it were whole is worse than no new generation at all, and the ledger already treats an
    unrebuilt bucket as owed rather than as a zero.
    """

    def __init__(self, reason: str, measured: float, limit: float) -> None:
        super().__init__(f"rebuild bound {reason} exceeded: {measured} > {limit}")
        self.reason = reason
        self.measured = measured
        self.limit = limit


def _rebuild_memory_bytes() -> int:
    """This process's anonymous memory plus what the kernel paged out of it.

    `RssAnon + VmSwap`, never `VmRSS` and never `Uss`. Both of those FALL when the kernel evicts
    pages, so a rebuild bound checked against either would let the process grow past its budget and
    still report healthy: measured on the live daemon, `VmRSS` sat 61.4% below `VmHWM` while
    `RssAnon + VmSwap` was within 1.1% of it. Anonymous pages are what a rebuild actually allocates,
    and swapping them out does not give them back.

    Returns 0 where `/proc` is not this shape, which disables the bound rather than inventing a
    number. A bound that cannot be measured must not be silently assumed satisfied OR violated, so
    the caller checks for a positive reading before comparing.
    """

    try:
        status = Path("/proc/self/status").read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError, OSError):
        return 0
    anon = swap = None
    for line in status.splitlines():
        if line.startswith("RssAnon:"):
            anon = int(line.split()[1]) * 1024
        elif line.startswith("VmSwap:"):
            swap = int(line.split()[1]) * 1024
    if anon is None or swap is None:
        return 0
    return anon + swap


def _require_rebuild_within_bounds(
    started: float,
    now: Callable[[], float],
    max_memory_bytes: int,
    max_seconds: float,
    memory_baseline: int,
    memory_reader: Callable[[], int] | None = None,
) -> None:
    """Stop a rebuild that has grown past its budget or run too long.

    These are the two bounds that end the REBUILD rather than the batch, so both raise. Checked
    both before and after every fetch, rather than between batches: a batch is up to 22,000 rows,
    and a rebuild that is already over budget should not be allowed to decode the rest of one. The
    check after the fetch is what closes the byte exit, where the batch ends inside the fetch that
    filled it and the caller would otherwise receive it unmeasured.

    Memory is the rebuild's GROWTH, `_rebuild_memory_bytes()` now minus the value read when the
    rebuild started, never the absolute reading. An absolute check bounds the whole process, which
    includes everything the process held before the rebuild existed -- on the live daemon that is
    1,529 MiB against a 192 MiB ceiling, so every rebuild would be refused forever. Growth is the
    quantity a rebuild controls and the only one it can be held to.

    A reading of zero at either end means `/proc` did not answer in the shape this expects. The
    bound is then not checked, and it is not assumed satisfied either -- there is nothing to
    compare. The lifetime bound still applies, so a runaway rebuild is still stopped on a platform
    without `RssAnon`.

    `memory_reader` is a seam for the same reason `now` is one on the lifetime bound. Growth is only
    observable when the allocator asks the kernel for pages, so in a warm process a small rebuild
    can decode thousands of rows and move `RssAnon` not at all. A test that needs the bound to fire
    injects a reader rather than allocating until the kernel happens to notice.
    """

    elapsed = now() - started
    if elapsed > max_seconds:
        raise RebuildBoundExceeded("rebuild_seconds", round(elapsed, 3), max_seconds)
    # Resolved here, not as a def-time default: the module attribute is a documented seam that
    # tests monkeypatch, and a default bound at definition would ignore the patch.
    memory = (memory_reader or _rebuild_memory_bytes)()
    if not memory or not memory_baseline:
        return
    grown = memory - memory_baseline
    if grown > max_memory_bytes:
        raise RebuildBoundExceeded("rebuild_memory_bytes", grown, max_memory_bytes)


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
    coverage_normalized: bool = False


@dataclass(frozen=True)
class PruneResult:
    observations_deleted: int
    coverage_epochs_deleted: int
    coverage_epochs_clipped: int
    usage_atoms_deleted: int
    source_generation: int
    unavailable_spans_deleted: int = 0
    unavailable_spans_clipped: int = 0
    retired_invalidations_deleted: int = 0

    @property
    def changed(self) -> int:
        """Facts removed. `retired_invalidations_deleted` is NOT one, and must never be added.

        Every caller uses this to decide whether to advance `source_generation` and record fresh
        invalidations. A retired ledger row records work that ALREADY completed -- no fact, no
        aggregate and no served bucket changes when it goes. Counting it here would advance the
        generation on every 60-second sweep that removed one row, immediately re-record
        invalidations for buckets whose facts never changed, and schedule a rebuild for them: the
        table would grow faster, not slower. Only fact deletion may advance generation.
        """
        return sum((
            self.observations_deleted,
            self.coverage_epochs_deleted,
            self.coverage_epochs_clipped,
            self.usage_atoms_deleted,
            self.unavailable_spans_deleted,
            self.unavailable_spans_clipped,
        ))


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
    retention_cutoff: float | None = None
    retention_prune: PruneResult | None = None


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
    # The RAW STORE's generation, read in the same transaction as the publication row above, so the
    # two can be compared without racing a concurrent append. ``source_generation`` is what the ring
    # was published FROM; this is what the store holds NOW.
    #
    # A lag between them is the ORDINARY steady state, not an error: publication coalesces on
    # RING_FLUSH_SECONDS, so the store is routinely ahead by up to one flush. What this field makes
    # possible is measuring that distance instead of guessing at it, which is the prerequisite for
    # the durable cursor and invalidation ledger that will eventually bound it.
    store_source_generation: int = 0
    # The buckets in THIS window with unapplied invalidations. Already read to hide them from
    # `rows`, so exposing them costs no extra query and gives the restart owner the durable work
    # list rather than making it re-derive one.
    pending_invalidations: tuple[int, ...] = ()

    @property
    def publication_lag(self) -> int:
        """How many store generations the published ring is behind. Zero when fully caught up."""
        return max(0, self.store_source_generation - self.source_generation)


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


def _stored_ring_bucket_json(bucket_json: str) -> str | bytes:
    raw = bucket_json.encode("utf-8")
    compressed = _RING_BUCKET_ZLIB_MAGIC + zlib.compress(
        raw,
        level=_RING_BUCKET_ZLIB_LEVEL,
    )
    return compressed if len(compressed) < len(raw) else bucket_json


def _decoded_ring_bucket_json(value: object) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, bytes) or not value.startswith(_RING_BUCKET_ZLIB_MAGIC):
        raise SchemaMismatchError("aggregate ring bucket encoding is unavailable")
    decompressor = zlib.decompressobj()
    try:
        raw = decompressor.decompress(
            value[len(_RING_BUCKET_ZLIB_MAGIC):],
            MAX_RING_BUCKET_BYTES + 1,
        )
    except zlib.error as error:
        raise SchemaMismatchError("aggregate ring bucket compression is invalid") from error
    if (
        len(raw) > MAX_RING_BUCKET_BYTES
        or not decompressor.eof
        or decompressor.unconsumed_tail
        or decompressor.unused_data
    ):
        raise SchemaMismatchError("aggregate ring bucket compression is invalid")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SchemaMismatchError("aggregate ring bucket compression is invalid") from error


def ring_slot_index(resolution_seconds: int, bucket_start: int) -> int:
    """The one owner of bucket-start -> ring slot address.

    Writes, reads, and every caller that needs to name a slot must agree exactly, because a ring
    IS its modular addressing: two copies that disagree by one write a bucket where a reader will
    never look for it.
    """
    return (bucket_start // resolution_seconds) % stats_resolution.RING_CAPACITIES[resolution_seconds]


def _ring_bucket_values(bucket: RingBucketWrite) -> tuple[int, int, int, str | bytes, int]:
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
    slot_index = ring_slot_index(resolution_seconds, bucket_start)
    return (
        resolution_seconds,
        slot_index,
        bucket_start,
        _stored_ring_bucket_json(bucket.bucket_json),
        int(bucket.complete),
    )


def _encode_json_object(value: Mapping[str, object], name: str) -> str:
    if not isinstance(value, Mapping):
        raise StorageValidationError(f"{name} must be a JSON object")
    try:
        return json.dumps(dict(value), allow_nan=False, separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError) as error:
        raise StorageValidationError(f"{name} must be a JSON object") from error


def _payload_byte_length(payload: object) -> int:
    """UTF-8 byte length of a stored payload, because the byte bound is named and derived in bytes.

    `len()` on a `str` counts CODE POINTS. SQLite hands TEXT back as `str`, so counting characters
    against `REBUILD_BATCH_MAX_DECODED_BYTES` admits a batch up to four times the size the budget
    was derived for.

    This store's own writer cannot produce the gap -- `_encode_json_object` uses `json.dumps` with
    the default `ensure_ascii=True`, so its payloads are ASCII and the two counts agree. The reader
    does not choose who wrote the database it opens, and a counter that is correct for exactly one
    writer is an assumption rather than a bound.

    `isascii()` is a scan with no allocation, so the common case pays no copy.
    """

    if isinstance(payload, (bytes, bytearray)):
        return len(payload)
    text = str(payload)
    return len(text) if text.isascii() else len(text.encode("utf-8"))


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


def _read_window(
    value: tuple[int | float, int | float] | None,
) -> tuple[float, float] | None:
    if value is None:
        return None
    if not isinstance(value, (tuple, list)) or len(value) != 2:
        raise StorageValidationError("read window must contain start and end")
    start = _validate_timestamp(value[0], "read window start")
    end = _validate_timestamp(value[1], "read window end")
    if end <= start:
        raise StorageValidationError("read window end must follow its start")
    return start, end


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


_COVERAGE_PREDECESSOR_SQL = (
    "SELECT family, source_id, epoch_id, started_at, ended_at, "
    "native_cadence_seconds, owner_generation FROM coverage_epochs "
    "INDEXED BY coverage_epochs_end "
    "WHERE family = ? AND source_id = ? AND ended_at IS NOT NULL "
    "AND ended_at <= ? ORDER BY ended_at DESC, started_at DESC, epoch_id DESC LIMIT 1"
)


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


def _schema_metadata(header: _Header) -> SchemaMetadata:
    return SchemaMetadata(
        header.schema_version,
        header.minimum_writer_protocol,
        header.minimum_writer_build,
        header.source_generation,
    )


def _read_migration_reconciliations(
    connection: sqlite3.Connection,
) -> tuple[MigrationReconciliation, ...]:
    rows = connection.execute(
        "SELECT migration_id, completed_at, source_digest, details_json "
        "FROM migration_reconciliation ORDER BY completed_at, migration_id"
    ).fetchall()
    return tuple(
        MigrationReconciliation(
            str(row[0]),
            float(row[1]),
            str(row[2]),
            _decode_json_object(row[3], "migration reconciliation details"),
        )
        for row in rows
    )


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


def invalidated_buckets(
    observed_range: tuple[float, float], *, end_exclusive: bool = False,
) -> tuple[tuple[int, int], ...]:
    """The exact (resolution, bucket_start) pairs one mutated time range makes stale.

    THE one range-to-buckets owner. Every producer that can contradict a published aggregate --
    a late observation, a usage tombstone, a prune, a coverage or unavailable-span change -- asks
    this instead of computing bucket boundaries itself. Per-producer copies of this arithmetic are
    how a resolution gets missed: the 1s ring would be invalidated and the 300s ring silently left
    serving the contradicted value.

    The range is INCLUSIVE at both ends. A fact exactly on a bucket boundary belongs to the bucket
    it starts, and a fact exactly at the end instant still lands in the bucket containing it.

    CLAMPED TO THE BOUNDED RING HORIZON, per resolution, and that clamp is the whole reason this is
    one owner rather than arithmetic each caller repeats.

    A ring holds exactly `RING_CAPACITIES[resolution]` slots, so nothing older than
    `resolution * capacity` before the range end can be stored OR served. A prune passes
    `(0, cutoff)` because it deletes everything below the cutoff, and expanding that literally
    walked from the Unix epoch: measured at a current cutoff, 2,001,470,467 pairs across the four
    resolutions, roughly 134 GiB, materialized in one list inside the mutating transaction. Every
    pair beyond the horizon named a slot that cannot exist and could never be retired.

    Clamping makes the result depend on the range's LENGTH, never on how far the epoch is from the
    start, so the worst case is `sum(RING_CAPACITIES.values())` pairs no matter how old the clock
    is.
    """
    start, end = observed_range
    if end < start:
        raise StorageValidationError("invalidated range end precedes its start")
    pairs: list[tuple[int, int]] = []
    for resolution_seconds, slot_count in sorted(stats_resolution.RING_CAPACITIES.items()):
        # A prune deletes facts strictly BELOW its cutoff, so its range is half-open. With an
        # inclusive end an aligned cutoff C both included bucket C -- which holds only facts at or
        # after C and is therefore untouched -- and omitted C-Nr at the far end, so the boundary
        # was wrong at BOTH ends by exactly one bucket. An inside-bucket cutoff still lands on the
        # bucket containing it, because that bucket really did lose facts.
        end_instant = max(0.0, end)
        if end_exclusive:
            if end_instant <= 0.0:
                continue
            # EXACT half-open math, never epsilon arithmetic. `end - 1e-9` is below the float ULP
            # at production epoch values: measured at cutoff 1_700_000_000 with r=1 it did not move
            # the value at all, so the excluded cutoff bucket was included. Deciding on the
            # remainder is exact at every magnitude.
            quotient = math.floor(end_instant / resolution_seconds)
            last = int(quotient) * resolution_seconds
            if last == end_instant:
                last -= resolution_seconds
            if last < 0:
                continue
        else:
            last = int(end_instant // resolution_seconds) * resolution_seconds
        horizon_start = last - (slot_count - 1) * resolution_seconds
        first = int(max(0.0, start) // resolution_seconds) * resolution_seconds
        first = max(first, horizon_start, 0)
        if first > last:
            continue
        pairs.extend(
            (resolution_seconds, bucket_start)
            for bucket_start in range(first, last + resolution_seconds, resolution_seconds)
        )
    return tuple(pairs)


def invalidated_buckets_for_instants(instants: Iterable[float]) -> tuple[tuple[int, int], ...]:
    """The exact buckets a SET of mutated instants touches, with no span between them.

    A batch is not an interval. Collapsing one to `(min, max)` and clamping to the ring horizon
    dropped genuinely contradicted old buckets whenever the same batch also carried a far-future
    timestamp: the clamp anchors to the newest instant, so a sparse batch of one old fact plus one
    future fact invalidated the future end and silently left the old published bucket serving
    contradicted data.

    Deriving per instant removes the span entirely. The result is bounded by
    `len(instants) * len(RING_CAPACITIES)` and is exact rather than conservative, so it also stops
    an ordinary two-point batch from invalidating everything between its ends.
    """
    pairs: set[tuple[int, int]] = set()
    for instant in instants:
        moment = max(0.0, float(instant))
        for resolution_seconds in stats_resolution.RING_CAPACITIES:
            pairs.add((resolution_seconds, int(moment // resolution_seconds) * resolution_seconds))
    return tuple(sorted(pairs))


def pending_invalidation_cells(
    connection: sqlite3.Connection,
) -> tuple[tuple[int, int, int], ...]:
    """Every unapplied invalidation as (resolution, bucket_start, source_generation).

    Bounded by the ring's fixed slot count, because actionability already guarantees each row names
    a populated slot. This is the DURABLE half of the dirty set: the service's in-memory
    `_pending_ring_dirty` does not survive a restart, so after one this ledger is the only record
    that those buckets still owe a rebuild.
    """
    return tuple(
        (int(row[0]), int(row[1]), int(row[2]))
        for row in connection.execute(
            "SELECT resolution_seconds, bucket_start, source_generation FROM ring_invalidations "
            "WHERE applied_at IS NULL ORDER BY resolution_seconds, bucket_start"
        )
    )


def _coverage_change_interval(
    previous: tuple[object, ...], current: tuple[object, ...],
) -> tuple[float, float | None]:
    """The exact range an in-place coverage-epoch update contradicts.

    `previous`/`current` are `(started_at, ended_at, native_cadence_seconds,
    owner_generation)`. Start and cadence are immutable here -- the caller has already
    rejected any change to them -- so the only coverage the row can gain or lose is at
    its END.

    * Extending a closed epoch from `p` to `c` newly claims `[p, c)`. Everything before
      `p` was already claimed by the same epoch at the same cadence and is untouched.
    * Closing an open epoch at `c` retracts the unbounded tail, so `[c, +inf)` changes.
    * A bare `owner_generation` change re-attributes the whole extent, which is rare
      (one per statsd restart) and is reported in full rather than guessed at.
    """

    started = float(current[0])
    previous_end = None if previous[1] is None else float(previous[1])
    current_end = None if current[1] is None else float(current[1])
    if previous[3] != current[3]:
        # Generation re-attributes the current extent, while closing a previously
        # open epoch also retracts its tail. The union is unbounded whenever either
        # side is open; otherwise it is the whole updated extent.
        return (started, None if previous_end is None or current_end is None else current_end)
    if previous_end == current_end:
        return (started, current_end)
    if previous_end is None:
        # Was `[started, +inf)`, now ends at `current_end`: the retracted tail changed.
        return (float(current_end), None)
    if current_end is None:
        # Reopening is rejected upstream; report the full extent rather than assume.
        return (started, None)
    return (previous_end, current_end)


def _slots_intersecting_intervals(
    connection: sqlite3.Connection, intervals: Iterable[tuple[float, float | None]],
) -> set[tuple[int, int]]:
    """Every currently published bucket whose half-open span intersects a changed half-open span.

    THE one interval-to-slot owner, for coverage epochs and unavailable spans alike. Flattening an
    interval to its two endpoints was wrong in both directions at once: a coverage change over
    `[6000, 6300)` recorded only `{6000, 6300}`, so the interior buckets 6060/6120/6180/6240 stayed
    FALSELY CLEAN while the exclusive end 6300 -- which the change never touched -- was marked.

    Derived FROM the persisted slots rather than by enumerating the interval. That is what makes an
    OPEN coverage epoch (`ended_at is None`, so `[started_at, +inf)`) expressible at all without an
    epoch-sized walk, and it keeps the result inherently actionable and bounded by the ring's fixed
    slot count.

    Both spans are half-open: a bucket `[b, b + r)` intersects `[start, end)` exactly when
    `b < end and b + r > start`. That single comparison is what excludes the end bucket and
    includes every interior one.
    """
    spans = [
        (max(0.0, float(start)), None if end is None else float(end))
        for start, end in intervals
    ]
    if not spans:
        return set()
    matched: set[tuple[int, int]] = set()
    for row in connection.execute(
        "SELECT resolution_seconds, bucket_start FROM aggregate_ring_slots "
        "WHERE bucket_json IS NOT NULL"
    ):
        resolution_seconds = int(row[0])
        bucket_start = int(row[1])
        bucket_end = bucket_start + resolution_seconds
        for span_start, span_end in spans:
            if bucket_end > span_start and (span_end is None or bucket_start < span_end):
                matched.add((resolution_seconds, bucket_start))
                break
    return matched


def _populated_ring_slots(
    connection: sqlite3.Connection, pairs: Iterable[tuple[int, int]],
) -> set[tuple[int, int]]:
    """Of these (resolution, bucket_start) pairs, the ones a populated slot actually holds.

    An invalidation is only meaningful when a PUBLISHED slot currently contradicts the facts. If no
    slot holds that bucket -- never published, cold, or already overwritten by ring wraparound --
    `read_ring_window` already reports it missing and routes it to the materializer, so a ledger row
    would add nothing and could never be retired: retirement happens in `publish_ring_buckets`, and
    nothing will republish a bucket the ring does not hold.

    Matching on `bucket_start` and not on slot index alone is what makes wraparound correct: a
    lapped slot holds a DIFFERENT bucket, so the old bucket is genuinely gone.
    """
    matched: set[tuple[int, int]] = set()
    for resolution_seconds, bucket_start in pairs:
        row = connection.execute(
            "SELECT 1 FROM aggregate_ring_slots WHERE resolution_seconds = ? AND bucket_start = ? "
            "AND bucket_json IS NOT NULL",
            (resolution_seconds, bucket_start),
        ).fetchone()
        if row is not None:
            matched.add((resolution_seconds, bucket_start))
    return matched


def _retire_unactionable_invalidations(connection: sqlite3.Connection) -> int:
    """Delete pending rows whose slot can no longer be rebuilt, in the caller's transaction.

    A pending row survives only while a concrete populated slot still holds its bucket. Once the
    ring has lapped past it, or the slot was cleared, no publication will ever retire it and the
    read path already reports that bucket missing -- so the row is pure accumulation, and this is
    what stopped repeated prunes leaking rows per generation.

    Deleting is safe precisely BECAUSE the slot is gone: there is no stale payload left to be
    falsely clean about. A row whose slot is still populated is never touched here.
    """
    stale = [
        (int(row[0]), int(row[1]))
        for row in connection.execute(
            "SELECT i.resolution_seconds, i.bucket_start FROM ring_invalidations AS i "
            "LEFT JOIN aggregate_ring_slots AS s "
            "ON s.resolution_seconds = i.resolution_seconds AND s.bucket_start = i.bucket_start "
            "AND s.bucket_json IS NOT NULL "
            "WHERE i.applied_at IS NULL AND s.bucket_start IS NULL"
        )
    ]
    if not stale:
        return 0
    connection.executemany(
        "DELETE FROM ring_invalidations WHERE resolution_seconds = ? AND bucket_start = ? "
        "AND applied_at IS NULL",
        stale,
    )
    return len(stale)


def _record_invalidations(
    connection: sqlite3.Connection,
    observed_range: tuple[float, float],
    *,
    reason: str,
    source_generation: int,
    now: float,
    end_exclusive: bool = False,
    instants: Iterable[float] | None = None,
    slots: set[tuple[int, int]] | None = None,
) -> int:
    """Record stale buckets INSIDE the caller's transaction, never after it.

    Recording after the mutation commits would leave a window in which the facts already
    contradict the ring and nothing says so; a crash inside that window loses the invalidation
    permanently, because the mutation that caused it has already been accounted for and will never
    be replayed. Same transaction or it is not durable.
    """
    if not _aggregate_tables(connection):
        return 0
    if slots is not None:
        # Already derived FROM persisted slots by the interval owner, so it is actionable by
        # construction and must not be re-intersected against a bucket enumeration.
        pairs = tuple(sorted(slots))
    else:
        pairs = (
            invalidated_buckets_for_instants(instants)
            if instants is not None
            else invalidated_buckets(observed_range, end_exclusive=end_exclusive)
        )
    # ACTIONABILITY, not just cardinality. The horizon clamp bounded each call to at most 1,248
    # rows but every one of them was still recorded whether or not a slot existed to rebuild, so a
    # cold store accumulated 1,252 permanently pending rows PER PRUNE -- measured 3,756 after three
    # cycles with zero populated slots and no publication at all.
    if slots is None:
        actionable = _populated_ring_slots(connection, pairs)
        pairs = tuple(pair for pair in pairs if pair in actionable)
    if not pairs:
        return 0
    connection.executemany(
        "INSERT OR IGNORE INTO ring_invalidations("
        "resolution_seconds, bucket_start, source_generation, reason, created_at, applied_at) "
        "VALUES(?, ?, ?, ?, ?, NULL)",
        [
            (resolution_seconds, bucket_start, int(source_generation), str(reason), float(now))
            for resolution_seconds, bucket_start in pairs
        ],
    )
    # AT MOST ONE PENDING ROW PER BUCKET. The primary key includes the generation, so a bucket
    # contradicted again at a newer generation gained a second pending row -- and a store that is
    # mutated repeatedly without republishing accumulated one per generation forever.
    #
    # Superseding is also the correct meaning, not merely the cheaper one: the bucket is
    # contradicted as of the NEWEST generation, and retirement already clears every pending row for
    # that bucket at or below the publishing generation, so the older rows carry no information the
    # newer one lacks. Only PENDING rows are collapsed; retired rows are history and stay.
    connection.executemany(
        "DELETE FROM ring_invalidations WHERE resolution_seconds = ? AND bucket_start = ? "
        "AND applied_at IS NULL AND source_generation < ?",
        [
            (resolution_seconds, bucket_start, int(source_generation))
            for resolution_seconds, bucket_start in pairs
        ],
    )
    return len(pairs)


def _aggregate_tables(connection: sqlite3.Connection) -> frozenset[str]:
    """Every table of the ring extension, matched by NAME SET rather than by prefix.

    The ring extension added `ring_replay_cursor` and `ring_invalidations`, which do not share the
    `aggregate_` prefix the original three were discovered by. Intersecting against `_RING_TABLES`
    keeps one list authoritative instead of coupling the extension's membership to a naming
    convention that a later table can silently fall outside of -- which is exactly how a partial
    ring shape would have been admitted as complete.
    """
    return frozenset(
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    ) & _RING_TABLES


def _coverage_conflict_reason(
    previous: tuple[object, ...] | None, current: tuple[object, ...],
) -> str | None:
    """SOLE owner of "does this offered epoch contradict the stored one".

    Both the applier and the read-only probe route through this. They were separate predicates
    and diverged: the probe modelled the unavailable-span overlap and an ordinary difference,
    but none of the three immutability rules below, so a batching caller acknowledged an epoch
    the commit would later reject -- and took a whole flush interval of other families' already
    acknowledged facts down with it. One parent, so they cannot drift apart again.

    `previous` is (started_at, ended_at, native_cadence_seconds, owner_generation) as stored, or
    None for an epoch that does not exist yet. `current` is the same tuple as offered.
    """

    if previous is None or tuple(previous) == current:
        return None
    if previous[0] != current[0] or previous[2] != current[2]:
        return "coverage epoch start and cadence are immutable"
    if previous[1] is not None and (current[1] is None or current[1] < previous[1]):
        return "coverage epoch end cannot move backward"
    if current[3] < previous[3]:
        return "coverage owner_generation cannot move backward"
    return None


def _require_exact_fixed_rows(
    connection: sqlite3.Connection,
    table: str,
    key_column: str,
    expected_keys: Iterable[int],
) -> None:
    """One owner for "this pre-allocated table holds exactly these rows, no more and no fewer".

    Several ring tables are seeded once at schema creation and never grow, so their row set is
    part of the schema rather than data. Each one carried its own hand-written check and
    `ring_replay_cursor` carried none at all: `_RING_FIXED_ROW_TABLES` excludes it because it is
    UPDATE-heavy and cannot take the reject triggers, and the validator then never queried it. A
    v8 file whose cursor rows an external tool dropped or added therefore passed validation and
    replayed from a wrong fold point. Growth was never the risk here - there is one INSERT site
    and no product DELETE - drift was, and drift needs an assertion rather than a trigger.

    `table` and `key_column` are module-level schema literals, never caller input.
    """

    found = tuple(
        int(row[0])
        for row in connection.execute(f"SELECT {key_column} FROM {table} ORDER BY {key_column}")
    )
    expected = tuple(sorted(expected_keys))
    if found != expected:
        raise SchemaMismatchError(
            f"{table} must hold exactly one row per {key_column} {list(expected)}, "
            f"found {list(found)}"
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
    _require_exact_fixed_rows(connection, "aggregate_publication", "singleton", (1,))
    # The cursor names the fold point each ring resumes from, so a dropped row silently refolds
    # that resolution from zero and an extra one folds a ring that does not exist. Neither is
    # reachable through the product, and both are reachable through an external tool.
    _require_exact_fixed_rows(
        connection, "ring_replay_cursor", "resolution_seconds", stats_resolution.RING_CAPACITIES
    )
    _require_exact_fixed_rows(
        connection, "aggregate_rings", "resolution_seconds", stats_resolution.RING_CAPACITIES
    )
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
            "AND tbl_name IN (SELECT name FROM sqlite_master WHERE type = 'table')"
        )
    )
    if trigger_names != _RING_TRIGGER_NAMES:
        raise SchemaMismatchError("aggregate ring fixed-row triggers do not match the exact schema")


# The cross-clock constraint shipped by the first schema-8 build. `created_at` is the OBSERVED
# instant of the causing fact and `applied_at` is the publication's WALL CLOCK, so ordering them
# rejected correct retirements whenever data time ran ahead of wall clock. New databases stopped
# carrying it, but `_validate_ring_schema` compares columns, rows and triggers -- never table SQL --
# so an EXISTING v8 kept it and still refuses valid replay today.
_RING_INVALIDATIONS_RETIRED_CHECK = "applied_at >= created_at"


def _ring_invalidations_needs_check_upgrade(connection: sqlite3.Connection) -> bool:
    """Whether this database still carries the retired cross-clock CHECK."""
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'ring_invalidations'"
    ).fetchone()
    return bool(row) and _RING_INVALIDATIONS_RETIRED_CHECK in str(row[0])


def _upgrade_ring_invalidations_check(connection: sqlite3.Connection) -> None:
    """Rebuild `ring_invalidations` without the retired CHECK, preserving every row.

    A table rebuild rather than an in-place edit because SQLite cannot drop a CHECK. The whole
    sequence runs in ONE transaction, so a crash leaves the old table intact rather than a
    half-migrated ledger: failure-atomic by construction, not by cleanup.

    Keeps the schema-version identity at v8 deliberately. The public contract -- table set,
    columns, capacities, triggers -- is unchanged; only an internal constraint that was always
    wrong is removed. Bumping the version would strand every existing v8 store behind a filename
    and socket change for a defect that is invisible to any consumer.

    Idempotent because the caller only invokes it when the retired text is present, and after the
    rebuild it is not.
    """
    with _transaction(connection):
        connection.execute(
            "CREATE TABLE ring_invalidations_upgraded ("
            "resolution_seconds INTEGER NOT NULL, "
            "bucket_start INTEGER NOT NULL CHECK (bucket_start >= 0), "
            "source_generation INTEGER NOT NULL CHECK (source_generation >= 0), "
            "reason TEXT NOT NULL, "
            "created_at REAL NOT NULL CHECK (created_at >= 0), "
            "applied_at REAL, "
            "PRIMARY KEY (resolution_seconds, bucket_start, source_generation), "
            "FOREIGN KEY (resolution_seconds) REFERENCES aggregate_rings(resolution_seconds), "
            "CHECK (bucket_start % resolution_seconds = 0), "
            "CHECK (applied_at IS NULL OR applied_at >= 0)"
            ") WITHOUT ROWID"
        )
        connection.execute(
            "INSERT INTO ring_invalidations_upgraded("
            "resolution_seconds, bucket_start, source_generation, reason, created_at, applied_at) "
            "SELECT resolution_seconds, bucket_start, source_generation, reason, created_at, "
            "applied_at FROM ring_invalidations"
        )
        connection.execute("DROP TABLE ring_invalidations")
        connection.execute("ALTER TABLE ring_invalidations_upgraded RENAME TO ring_invalidations")
        connection.execute(
            "CREATE INDEX ring_invalidations_pending "
            "ON ring_invalidations(resolution_seconds, bucket_start) WHERE applied_at IS NULL"
        )


def _initialize_ring_schema(connection: sqlite3.Connection) -> None:
    tables = _aggregate_tables(connection)
    if tables:
        # Before validation, because the retired CHECK is exactly what an existing store carries
        # and validation does not inspect table SQL at all.
        if _ring_invalidations_needs_check_upgrade(connection):
            _upgrade_ring_invalidations_check(connection)
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
            "payload_version INTEGER NOT NULL DEFAULT 0 CHECK (payload_version >= 0), "
            "PRIMARY KEY (resolution_seconds, slot_index), "
            "FOREIGN KEY (resolution_seconds) REFERENCES aggregate_rings(resolution_seconds), "
            "CHECK (bucket_start IS NULL OR bucket_start >= 0), "
            "CHECK (bucket_start IS NULL OR bucket_start % resolution_seconds = 0), "
            "CHECK ((bucket_start IS NULL AND bucket_json IS NULL AND complete = 0 "
            "AND source_generation = 0 AND ring_generation = 0 AND published_at = 0 "
            "AND payload_version = 0) "
            "OR (bucket_start IS NOT NULL AND bucket_json IS NOT NULL AND payload_version > 0))) WITHOUT ROWID"
        )
        connection.execute(
            "CREATE TABLE ring_replay_cursor ("
            "resolution_seconds INTEGER PRIMARY KEY, "
            "folded_through_observed_at REAL NOT NULL DEFAULT 0 "
            "CHECK (folded_through_observed_at >= 0), "
            "folded_source_generation INTEGER NOT NULL DEFAULT 0 "
            "CHECK (folded_source_generation >= 0), "
            "updated_at REAL NOT NULL DEFAULT 0 CHECK (updated_at >= 0), "
            "CHECK (resolution_seconds > 0), "
            "FOREIGN KEY (resolution_seconds) REFERENCES aggregate_rings(resolution_seconds)"
            ") WITHOUT ROWID"
        )
        connection.execute(
            "CREATE TABLE ring_invalidations ("
            "resolution_seconds INTEGER NOT NULL, "
            "bucket_start INTEGER NOT NULL CHECK (bucket_start >= 0), "
            "source_generation INTEGER NOT NULL CHECK (source_generation >= 0), "
            "reason TEXT NOT NULL, "
            "created_at REAL NOT NULL CHECK (created_at >= 0), "
            "applied_at REAL, "
            "PRIMARY KEY (resolution_seconds, bucket_start, source_generation), "
            "FOREIGN KEY (resolution_seconds) REFERENCES aggregate_rings(resolution_seconds), "
            "CHECK (bucket_start % resolution_seconds = 0), "
            # `created_at` and `applied_at` are DIFFERENT CLOCKS and must not be ordered against
            # each other. `created_at` is the OBSERVED instant of the fact that caused the
            # staleness -- data time, so this module needs no ambient clock -- while `applied_at`
            # is the publication's wall clock. Ordering them rejected correct retirements whenever
            # data time ran ahead of wall clock, which is routine for a store replaying history.
            "CHECK (applied_at IS NULL OR applied_at >= 0)"
            ") WITHOUT ROWID"
        )
        # Outstanding work first: a replay that must find unapplied rows cannot afford a scan of
        # every retired one, and this is the only index the ledger needs.
        connection.execute(
            "CREATE INDEX ring_invalidations_pending "
            "ON ring_invalidations(resolution_seconds, bucket_start) WHERE applied_at IS NULL"
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
        # After aggregate_rings, because both schema-8 tables carry a foreign key to it and
        # `foreign_keys` is ON for every connection this store opens.
        connection.executemany(
            "INSERT INTO ring_replay_cursor(resolution_seconds) VALUES(?)",
            ((resolution_seconds,) for resolution_seconds in stats_resolution.RING_CAPACITIES),
        )
        connection.executemany(
            "INSERT INTO aggregate_ring_slots(resolution_seconds, slot_index) VALUES(?, ?)",
            (
                (resolution_seconds, slot_index)
                for resolution_seconds, slot_count in stats_resolution.RING_CAPACITIES.items()
                for slot_index in range(slot_count)
            ),
        )
        for table in sorted(_RING_FIXED_ROW_TABLES):
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
        include_browser_diagnostics: bool = True,
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
            # The current format makes the ring extension part of the FORMAT rather than an opt-in a caller
            # remembers to request. The durability kernel -- replay cursor and invalidation ledger
            # -- has to exist before the first append can be recorded as un-folded, and an optional
            # kernel is one that is absent exactly when a crash needs it. Creation is idempotent:
            # an existing extension is validated, not rebuilt.
            _initialize_ring_schema(connection)
            if include_browser_diagnostics:
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
        include_browser_diagnostics: bool = True,
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
            if include_browser_diagnostics:
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
        advance_publication: bool = True,
    ) -> RingPublication:
        """Replace exact addressed slots, optionally reserving the next public generation."""

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
            # A slot about to be REWRITTEN may currently hold a different bucket. That older
            # bucket is displaced by this write and can never be rebuilt from the ring again, so
            # any pending row naming it is dead the moment the write lands. Cleanup previously ran
            # only inside prune, so a full-lap overwrite followed by a no-op prune left the row
            # forever. Reading the occupant BEFORE the update is what makes this exact.
            displaced: list[tuple[int, int]] = []
            for resolution_seconds, slot_index, bucket_start, _json, _complete in prepared:
                occupant = connection.execute(
                    "SELECT bucket_start FROM aggregate_ring_slots "
                    "WHERE resolution_seconds = ? AND slot_index = ? AND bucket_json IS NOT NULL",
                    (resolution_seconds, slot_index),
                ).fetchone()
                if occupant is not None and occupant[0] is not None and int(occupant[0]) != bucket_start:
                    displaced.append((resolution_seconds, int(occupant[0])))
            for resolution_seconds, slot_index, bucket_start, bucket_json, complete in prepared:
                changed = connection.execute(
                    "UPDATE aggregate_ring_slots SET bucket_start = ?, bucket_json = ?, "
                    "complete = ?, source_generation = ?, ring_generation = ?, published_at = ?, "
                    "payload_version = ? "
                    "WHERE resolution_seconds = ? AND slot_index = ?",
                    (
                        bucket_start, bucket_json, complete, source, ring_generation, published,
                        RING_PAYLOAD_VERSION, resolution_seconds, slot_index,
                    ),
                ).rowcount
                if changed != 1:
                    raise SchemaMismatchError("aggregate ring slot address is missing")
                newest_by_resolution[resolution_seconds] = max(
                    bucket_start,
                    newest_by_resolution.get(resolution_seconds, bucket_start),
                )
            # Only the DISPLACED buckets, and only their PENDING rows. The newly written bucket is
            # deliberately untouched here: its own contradiction is settled by the generation-gated
            # retirement below, not by having been overwritten.
            if displaced:
                connection.executemany(
                    "DELETE FROM ring_invalidations WHERE resolution_seconds = ? AND bucket_start = ? "
                    "AND applied_at IS NULL",
                    displaced,
                )
            # Retire each invalidation in the SAME transaction as the republication that answers
            # it, and only for the exact buckets actually rewritten. This remains true for a
            # bounded startup repair; its corrected slots reserve the next ring generation while
            # the public singleton stays on the prior coherent generation until the ordinary flush.
            connection.executemany(
                "UPDATE ring_invalidations SET applied_at = ? "
                "WHERE resolution_seconds = ? AND bucket_start = ? AND applied_at IS NULL "
                # Retirement authority is the SOURCE GENERATION, never the wall clock. A
                # publication built from a generation-N snapshot cannot contain facts that arrived
                # at N+1, so it must not clear their invalidation merely because its clock is
                # later -- which `created_at <= published_at` allowed, marking a bucket reconciled
                # that the publication demonstrably could not account for.
                "AND source_generation <= ?",
                [
                    (published, resolution_seconds, bucket_start, source)
                    for resolution_seconds, slot_index, bucket_start, bucket_json, complete in prepared
                ],
            )
            # The cursor advances only AFTER those effects are staged in this same transaction, so
            # it can never claim work that a crash then loses. It records the newest instant this
            # ring has folded, per resolution.
            connection.executemany(
                "UPDATE ring_replay_cursor SET folded_through_observed_at = max(folded_through_observed_at, ?), "
                "folded_source_generation = ?, updated_at = ? "
                # Gated on GENERATION, not on the horizon moving. A republication of the same
                # bucket at a newer generation is exactly what replaying contradicted work looks
                # like, and the horizon does not move for it -- so a horizon-gated update left
                # `folded_source_generation` stale precisely when it mattered most.
                "WHERE resolution_seconds = ? AND folded_source_generation <= ?",
                [
                    (
                        float(newest + resolution_seconds), source, published,
                        resolution_seconds, source,
                    )
                    for resolution_seconds, newest in newest_by_resolution.items()
                ],
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
            if advance_publication:
                changed = connection.execute(
                    "UPDATE aggregate_publication SET ring_generation = ?, source_generation = ?, "
                    "published_at = ? WHERE singleton = 1",
                    (ring_generation, source, published),
                ).rowcount
                if changed != 1:
                    raise SchemaMismatchError("aggregate publication row is missing")
        return RingPublication(ring_generation, source, published, len(prepared))

    def retire_unrebuildable_ring_cells(
        self,
        cells: Iterable[tuple[int, int, int]],
    ) -> tuple[tuple[int, int], ...]:
        """Clear an exact contradicted slot and retire its observed pending row, together.

        The ONE owner of the honest-gap transition. A pending invalidation is answered by a
        republication (`publish_ring_buckets`), and that is the only path that may retire a row
        whose bucket is still rebuildable. But a bucket that has aged out of the materializer's
        candidate window can never be rebuilt again: startup repair stages it, no candidate bucket
        exists to write, no publication answers it, and the row stays pending forever while
        `read_ring_window` hides the slot. The retained payload is then both permanently hidden and
        permanently contradicted.

        The serving decision is an honest gap: drop the contradicted payload and the row it owes in
        ONE transaction, so there is never a moment where the slot is served without its
        contradiction or the row survives its slot. Retirement is a DELETE, matching
        `_retire_unactionable_invalidations`, because no publication ever answered this row --
        stamping `applied_at` would claim a republication that did not happen.

        Exactness is the whole safety argument. The caller passes the pending row's observed source
        generation, and the transaction proceeds only while that exact row is still pending and
        the slot still holds the older bucket it contradicts. A row another publisher retired or
        replaced, or a slot that publisher rebuilt or lapped, leaves stale repair work no authority
        over the new state. Callers must only pass rows they measured to be unrebuildable.
        """
        if self.read_only:
            raise StatsCurrentError("stats store reader cannot retire ring cells")
        addressed = sorted({
            (
                _validate_nonnegative_integer(resolution_seconds, "resolution_seconds"),
                _validate_nonnegative_integer(bucket_start, "bucket_start"),
                _validate_nonnegative_integer(source_generation, "source_generation"),
            )
            for resolution_seconds, bucket_start, source_generation in cells
        })
        if not addressed:
            return ()
        connection = self._require_ring_storage()
        retired: list[tuple[int, int]] = []
        with _transaction(connection):
            for resolution_seconds, bucket_start, source_generation in addressed:
                cleared = connection.execute(
                    "UPDATE aggregate_ring_slots SET bucket_start = NULL, bucket_json = NULL, "
                    "complete = 0, source_generation = 0, ring_generation = 0, published_at = 0, "
                    "payload_version = 0 "
                    "WHERE resolution_seconds = ? AND bucket_start = ? AND bucket_json IS NOT NULL "
                    "AND source_generation < ? AND EXISTS ("
                    "SELECT 1 FROM ring_invalidations WHERE resolution_seconds = ? "
                    "AND bucket_start = ? AND source_generation = ? AND applied_at IS NULL)",
                    (
                        resolution_seconds, bucket_start, source_generation,
                        resolution_seconds, bucket_start, source_generation,
                    ),
                ).rowcount
                if not cleared:
                    # Retired/replaced work, a rebuilt slot, or a lapped slot is a different
                    # identity, so this stale observation has no authority over either half.
                    continue
                deleted = connection.execute(
                    "DELETE FROM ring_invalidations WHERE resolution_seconds = ? "
                    "AND bucket_start = ? AND source_generation = ? AND applied_at IS NULL",
                    (resolution_seconds, bucket_start, source_generation),
                ).rowcount
                if deleted != 1:
                    raise SchemaMismatchError(
                        "pending ring invalidation changed inside honest-gap transaction"
                    )
                retired.append((resolution_seconds, bucket_start))
        return tuple(retired)

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
        with _transaction(connection):
            store_row = connection.execute(
                "SELECT source_generation FROM schema_meta WHERE singleton = 1"
            ).fetchone()
            if store_row is None:
                raise SchemaMismatchError("stats schema metadata row is missing")
            store_generation = int(store_row[0])
            slot_rows = {
                int(row[0]): row
                for row in connection.execute(
                    "SELECT slot_index, bucket_start, bucket_json, complete, "
                    "source_generation, ring_generation, published_at, payload_version "
                    "FROM aggregate_ring_slots WHERE resolution_seconds = ?",
                    (resolution_value,),
                )
            }
            # aggregate_publication allocates a process-wide transaction generation, but a
            # transaction may update only a subset of resolutions. The newest populated physical
            # slot is therefore the durable publication cursor for THIS resolution. Using the
            # global allocator here made a 1s/10s-only publication falsely invalidate still-current
            # 60s/300s windows and sent their requests back to an older warm-cache body.
            latest_slot = max(
                (
                    row
                    for row in slot_rows.values()
                    if row[1] is not None and row[2] is not None
                ),
                key=lambda row: int(row[5]),
                default=None,
            )
            resolution_publication = (
                (0, 0, 0.0)
                if latest_slot is None
                else (int(latest_slot[5]), int(latest_slot[4]), float(latest_slot[6]))
            )
            # Bounded by the requested window, not by the whole ledger: a store that has
            # accumulated invalidations outside this window must not make this read grow.
            stale_starts = {
                int(row[0])
                for row in connection.execute(
                    "SELECT bucket_start FROM ring_invalidations "
                    "WHERE applied_at IS NULL AND resolution_seconds = ? "
                    "AND bucket_start >= ? AND bucket_start < ?",
                    (resolution_value, window_start, end),
                )
            }
            rows: list[RingBucketRow] = []
            missing: list[int] = []
            for bucket_start in expected_starts:
                slot_index = ring_slot_index(resolution_value, bucket_start)
                candidate = slot_rows.get(slot_index)
                if candidate is None or candidate[1] is None or int(candidate[1]) != bucket_start:
                    missing.append(bucket_start)
                    continue
                if bucket_start in stale_starts:
                    # A fact mutation has contradicted this published bucket and no republication
                    # has happened yet. Serving it would answer with an aggregate the store's own
                    # facts disagree with, which is worse than answering with a gap: the gap is
                    # visibly incomplete, the stale bucket is confidently wrong. Reporting it
                    # MISSING routes it to the materializer, which rebuilds it from the facts.
                    missing.append(bucket_start)
                    continue
                if int(candidate[7]) != RING_PAYLOAD_VERSION:
                    # Shape-only acceptance ends here. Serving decodes named fields out of
                    # `bucket_json`, so a blob written under a different payload contract would be
                    # mis-decoded into a chart that looks plausible and is wrong. Reporting the
                    # bucket as MISSING routes it to the materializer, which rebuilds it from facts
                    # that are still authoritative, rather than refusing the whole window.
                    missing.append(bucket_start)
                    continue
                rows.append(
                    RingBucketRow(
                        resolution_value,
                        bucket_start,
                        _decoded_ring_bucket_json(candidate[2]),
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
            resolution_publication[1],
            resolution_publication[0],
            resolution_publication[2],
            store_generation,
            tuple(sorted(stale_starts)),
        )

    def pending_invalidation_cells(self) -> tuple[tuple[int, int, int], ...]:
        """Every unapplied invalidation as (resolution, bucket_start, source_generation).

        A PUBLIC method rather than a module function taking `_connection()`, because the service
        is the caller and reaching through a private accessor both breaks the owner boundary and
        breaks every store double that legitimately does not have one.

        Bounded by the ring's fixed slot count: actionability already guarantees each row names a
        populated slot. This is the DURABLE half of the dirty set, and after a restart it is the
        only record that those buckets still owe a rebuild.
        """
        return pending_invalidation_cells(self._connection())

    # Dispositions a caller can learn WITHOUT committing. `_apply_observations` decides
    # accepted/duplicate/conflict with a SELECT and only then INSERTs; a batching caller that
    # must answer its RPC before the transaction runs needs the SELECT half on its own.
    OBSERVATION_ACCEPTED = "accepted"
    OBSERVATION_DUPLICATE = "duplicate"
    OBSERVATION_CONFLICT = "conflict"

    def observation_dispositions(
        self, observations: Iterable[Observation],
    ) -> tuple[str, ...]:
        """Report what a commit WOULD decide for each observation, writing nothing.

        Read-only and side-effect free, so it is safe on a reader and safe to call before the
        decision to commit has been made. The answer is only authoritative while the caller
        holds the sole-writer lock, because a concurrent commit could otherwise land between
        this probe and the transaction it is predicting.
        """

        connection = self._connection()
        verdicts: list[str] = []
        for observation in observations:
            values = _observation_values(observation)
            previous = connection.execute(
                "SELECT observed_at, epoch_id, owner_generation, payload_json FROM observations "
                "WHERE event_id = ? AND family = ? AND source_id = ?", values[:3],
            ).fetchone()
            if previous is None:
                verdicts.append(self.OBSERVATION_ACCEPTED)
            elif tuple(previous) == values[3:]:
                verdicts.append(self.OBSERVATION_DUPLICATE)
            else:
                verdicts.append(self.OBSERVATION_CONFLICT)
        return tuple(verdicts)

    COVERAGE_CHANGED = "changed"
    COVERAGE_UNCHANGED = "unchanged"
    COVERAGE_CONFLICT = "conflict"

    def coverage_dispositions(
        self, coverage: Iterable[CoverageEpoch],
    ) -> tuple[str, ...]:
        """Report what a commit WOULD decide for each coverage epoch, writing nothing.

        Mirrors `_apply_coverage_epochs`: an offer that matches the stored row changes nothing,
        and an offer overlapping an unavailable span is rejected. A caller that batches must
        not report an unchanged re-offer as accepted -- a live collector re-offers its OPEN
        epoch every cadence tick, so that error would inflate every acknowledgement.
        """

        connection = self._connection()
        verdicts: list[str] = []
        for epoch in coverage:
            values = _coverage_values(epoch)
            current = values[3:]
            conflict = connection.execute(
                "SELECT 1 FROM unavailable_spans WHERE family = ? AND source_id = ? "
                "AND ended_at > ? AND (? IS NULL OR started_at < ?) LIMIT 1",
                (values[0], values[1], current[0], current[1], current[1]),
            ).fetchone()
            if conflict is not None:
                verdicts.append(self.COVERAGE_CONFLICT)
                continue
            previous = connection.execute(
                "SELECT started_at, ended_at, native_cadence_seconds, owner_generation "
                "FROM coverage_epochs WHERE family = ? AND source_id = ? AND epoch_id = ?",
                values[:3],
            ).fetchone()
            if _coverage_conflict_reason(previous, current) is not None:
                verdicts.append(self.COVERAGE_CONFLICT)
            elif previous is not None and tuple(previous) == current:
                verdicts.append(self.COVERAGE_UNCHANGED)
            else:
                verdicts.append(self.COVERAGE_CHANGED)
        return tuple(verdicts)

    def last_vacuumed_at(self) -> float:
        """Return the persisted completion time for the last successful VACUUM."""
        row = self._connection().execute(
            "SELECT last_vacuumed_at FROM schema_meta WHERE singleton = 1"
        ).fetchone()
        if row is None:
            raise SchemaMismatchError("current stats vacuum metadata is missing")
        return float(row[0])

    def reclaimable_ratio(self) -> float:
        """Fraction of the current file a rewrite could physically hand back, right now.

        Two kinds of waste, and BOTH are needed. `freelist_count` counts pages a delete already
        released, and `dbstat.unused` counts the slack inside pages that are still live. A metric
        built on the free list alone reads zero for a database bloated purely by partially-filled
        B-tree pages, which is what random-key inserts produce and is the exact case this guard
        exists to catch.

        This is the RAW figure and it is not a compaction forecast on its own. Every schema has a
        natural fill, so a freshly vacuumed store still reads well above zero -- audited at 3.600%,
        3.864%, 3.929% and 3.576% on four databases whose truly recoverable space was 0.0000%. Use
        `reclaimable_ratio() - reclaimable_ratio_at_last_vacuum()` for anything that decides.
        """

        connection = self._connection()
        page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
        page_count = int(connection.execute("PRAGMA page_count").fetchone()[0])
        if page_size <= 0 or page_count <= 0:
            return 0.0
        freelist_pages = int(connection.execute("PRAGMA freelist_count").fetchone()[0])
        # COALESCE because `dbstat` is empty for a database with no pages yet, and SUM of nothing
        # is NULL rather than 0.
        unused_bytes = int(connection.execute(
            "SELECT COALESCE(SUM(unused), 0) FROM dbstat"
        ).fetchone()[0])
        return (unused_bytes + freelist_pages * page_size) / (page_count * page_size)

    def reclaimable_ratio_at_last_vacuum(self) -> float:
        """The baseline the last successful vacuum recorded, or 0.0 when it is not knowable.

        0.0 is the deliberate fallback for a never-vacuumed store, an unreadable or malformed
        sidecar, and a sidecar that names a different vacuum than the database does. It makes the
        benefit read as the raw ratio, which OVER-states benefit rather than under-stating it: an
        unknown baseline can then cost one unnecessary rewrite, which self-corrects because that
        rewrite writes both halves again, whereas a fabricated high baseline would suppress a
        needed compaction indefinitely and leave the disk to fill.
        """

        try:
            value = read_json_file(
                self.path.parent / VACUUM_BASELINE_FILENAME, None, exceptions=(FileNotFoundError,)
            )
        except (OSError, json.JSONDecodeError):
            # Maintenance metadata, not facts. Unreadable means "baseline unknown", never "skip".
            return 0.0
        if not isinstance(value, dict):
            return 0.0
        recorded_marker = value.get("last_vacuumed_at")
        recorded_ratio = value.get("reclaimable_ratio")
        for candidate in (recorded_marker, recorded_ratio):
            if isinstance(candidate, bool) or not isinstance(candidate, (int, float)):
                return 0.0
            if not math.isfinite(float(candidate)) or float(candidate) < 0:
                return 0.0
        # A ratio is a fraction of the file, so above 1.0 is impossible. `_record_vacuum_baseline`
        # is bounded by construction and cannot write one, which makes this corruption -- and an
        # unbounded high baseline is exactly the "suppress a needed compaction indefinitely" case
        # this docstring names. The marker beside it is a timestamp and has no upper bound.
        if float(recorded_ratio) > 1.0:
            return 0.0
        # The agreement check. Without it a crash between the marker and this file would present
        # the previous rewrite's baseline as though it described the current one.
        if float(recorded_marker) != self.last_vacuumed_at():
            return 0.0
        return float(recorded_ratio)

    def _record_vacuum_baseline(self, completed_at: float, ratio: float) -> None:
        atomic_write_text(
            self.path.parent / VACUUM_BASELINE_FILENAME,
            json.dumps(
                {"last_vacuumed_at": completed_at, "reclaimable_ratio": ratio},
                sort_keys=True, separators=(",", ":"),
            ) + "\n",
            mode=0o600,
        )

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
        # LAST, and measured against the final physical state rather than the one before the marker
        # transaction, so `reclaimable_ratio()` on an untouched store reads back exactly this
        # number and the benefit is exactly 0.0 rather than a small drift. Ordering it after the
        # marker is safe precisely because the sidecar carries the marker value: a crash here
        # leaves a detectable disagreement, not a plausible wrong answer.
        self._record_vacuum_baseline(timestamp, self.reclaimable_ratio())
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
    ) -> tuple[int, tuple[tuple[float, float | None], ...]]:
        """Apply coverage rows and return only the time ranges they actually changed.

        The returned ranges -- not the offered rows -- are what invalidates published
        buckets. A live collector re-offers its OPEN epoch every cadence tick with
        `ended_at` advanced by one cadence, so the row's full extent is its whole
        lifetime while the real change is the last tick. Reporting the extent made a
        single 1-second extension of a 31.8-hour `cpu` epoch invalidate all 1,248
        populated ring slots instead of the four buckets it touched, measured on a live
        7220 store. The ring was therefore 100% dirty at every append and no slot was
        ever reusable. Readiness is only announced after `_repair_startup_owed_slots`
        republishes every pending slot, so a ledger that refilled to the full ring once
        per second could never drain: statsd sat at 73% CPU and the browser reported
        `current stats readiness timed out after 10000ms`, which is why no graph, cost,
        or token panel could load.
        """

        changed = 0
        intervals: list[tuple[float, float | None]] = []
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
                # A brand-new epoch claims its whole extent for the first time.
                intervals.append((
                    float(current[0]), None if current[1] is None else float(current[1]),
                ))
            elif tuple(previous) != current:
                reason = _coverage_conflict_reason(previous, current)
                if reason is not None:
                    raise StorageValidationError(reason)
                connection.execute(
                    "UPDATE coverage_epochs SET ended_at = ?, owner_generation = ? "
                    "WHERE family = ? AND source_id = ? AND epoch_id = ?", (current[1], current[3], *key),
                )
                changed += 1
                intervals.append(_coverage_change_interval(previous, current))
            # An identical re-offer changed nothing, so it contradicts no published
            # bucket and contributes no interval. This is the common case: it is what
            # every healthy collector does on every tick.
        return changed, tuple(intervals)

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
    ) -> tuple[int, int, tuple[tuple[object, ...], ...], tuple[float, ...]]:
        """Also return the observed instants of the atoms actually DELETED.

        The caller needs the range to invalidate, and it is this function -- the only code that
        knows which tombstones matched a stored atom -- that can name it. Returning it here removes
        the caller's need to index a prepared tuple at all: that indexing read the last field,
        which is `thread_id`, so a tombstone-only append invalidated nothing.
        """
        accepted = duplicate = 0
        accepted_values: list[tuple[object, ...]] = []
        deleted_observed_at: list[float] = []
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
            # `previous[0]` is the stored atom's own observed instant, and the equality check above
            # has already proven it equals the tombstone's, so this is the deleted fact's time.
            deleted_observed_at.append(float(previous[0]))
        return accepted, duplicate, tuple(accepted_values), tuple(deleted_observed_at)

    def _apply_unavailable_spans(
        self, connection: sqlite3.Connection, prepared: tuple[tuple[object, ...], ...],
    ) -> tuple[int, tuple[tuple[float, float | None], ...]]:
        """Apply spans and return the ranges they actually added.

        A span is keyed by its exact endpoints, so an accepted row is always new and
        always contradicts its whole range. A re-offered identical row stored nothing
        and must not invalidate a bucket that is still correct.
        """

        accepted = 0
        intervals: list[tuple[float, float | None]] = []
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
                intervals.append((float(values[3]), float(values[4])))
            elif tuple(previous) != values[5:]:
                raise StorageValidationError("unavailable span identity conflicts with stored data")
        return accepted, tuple(intervals)

    def append_batch(
        self,
        *,
        observations: Iterable[Observation] = (),
        coverage_epochs: Iterable[CoverageEpoch] = (),
        usage_atoms: Iterable[UsageAtom] = (),
        usage_tombstones: Iterable[UsageAtomTombstone] = (),
        unavailable_spans: Iterable[UnavailableSpan] = (),
        retention_now: float | None = None,
    ) -> AppendResult:
        """Commit one deduplicated source batch and enforce optional retention."""

        if self.read_only:
            raise StatsCurrentError("stats store reader cannot mutate the database")
        if retention_now is not None:
            _require_retention_covers_display_window()

        prepared_observations = tuple(_observation_values(item) for item in observations)
        prepared_coverage = tuple(_coverage_values(item) for item in coverage_epochs)
        prepared_usage = tuple(_usage_values(item) for item in usage_atoms)
        prepared_tombstones = tuple(
            _usage_tombstone_values(item) for item in usage_tombstones
        )
        prepared_unavailable = tuple(_unavailable_values(item) for item in unavailable_spans)
        retention_cutoff = (
            None
            if retention_now is None
            else _validate_timestamp(retention_now, "retention_now") - RETENTION_SECONDS
        )
        connection = self._connection()
        observations_accepted = coverage_changed = usage_accepted = unavailable_accepted = 0
        tombstones_accepted = tombstones_duplicate = usage_attribution_conflicts = 0
        retention_prune: PruneResult | None = None
        with _transaction(connection):
            generation = int(connection.execute(
                "SELECT source_generation FROM schema_meta WHERE singleton = 1"
            ).fetchone()[0])
            observations_accepted, accepted_observations = self._apply_observations(
                connection, prepared_observations,
            )
            _append_browser_diagnostics(connection, accepted_observations)
            coverage_changed, coverage_intervals = self._apply_coverage_epochs(
                connection, prepared_coverage,
            )
            usage_accepted, usage_attribution_conflicts, accepted_usage = self._apply_usage_atoms(
                connection, prepared_usage,
            )
            (
                tombstones_accepted, tombstones_duplicate, accepted_tombstones,
                tombstoned_observed_at,
            ) = self._apply_usage_tombstones(connection, prepared_tombstones)
            unavailable_accepted, unavailable_intervals = self._apply_unavailable_spans(
                connection, prepared_unavailable,
            )
            if retention_cutoff is not None:
                retention_prune = _prune_retained_facts(connection, retention_cutoff)
            changed = (
                observations_accepted + coverage_changed + usage_accepted
                + unavailable_accepted + tombstones_accepted
                + (0 if retention_prune is None else retention_prune.changed)
            )
            if changed:
                generation += 1
                connection.execute(
                    "UPDATE schema_meta SET source_generation = ? WHERE singleton = 1",
                    (generation,),
                )
                # Same transaction as the facts that caused it. The observed range is taken from
                # what was ACCEPTED, not from what was offered: a rejected duplicate changes no
                # aggregate and must not invalidate a bucket that is still correct.
                # Timestamp positions are DERIVED from `_COLUMNS`, the single owner of each
                # table's column order, rather than written out here. Hand-indexing three
                # different tuple layouts would be a fourth copy of that contract and would break
                # silently the next time one of them gains a column.
                #
                # Prepared is a superset of accepted, so a batch containing rejected duplicates
                # invalidates slightly more than it strictly must. That direction is deliberate:
                # over-invalidating costs one rebuild from authoritative facts, while
                # under-invalidating leaves a contradicted bucket being served as current.
                # POINT facts contribute instants; INTERVAL facts contribute spans. Coverage
                # epochs and unavailable spans are intervals, and flattening them to their two
                # endpoints left every interior bucket falsely clean while marking an exclusive end
                # the change never touched.
                mutated: list[float] = []
                for table, rows in (
                    ("observations", prepared_observations),
                    ("usage_atoms", prepared_usage),
                ):
                    index = _COLUMNS[table].index("observed_at")
                    mutated.extend(float(row[index]) for row in rows if row[index] is not None)
                # INTERVAL facts report what they CHANGED, not their stored extent. The
                # appliers above are the only code that can tell an extension from an
                # identical re-offer, so they own this and `prepared_*` is deliberately
                # not consulted: a live collector re-offers the same epoch row every
                # tick, and reading the offered extent invalidated the epoch's entire
                # lifetime -- the whole published ring -- once per second.
                # `ended_at is None` is still an OPEN claim running to the present and is
                # carried as an unbounded interval rather than collapsed to its start.
                changed_intervals: list[tuple[float, float | None]] = [
                    *coverage_intervals,
                    *unavailable_intervals,
                ]
                # From the deletion owner, which is the only code that knows which tombstones
                # actually matched a stored atom. Not conditional on `mutated` being empty either:
                # a batch that both appends and tombstones contradicts BOTH ranges.
                mutated.extend(tombstoned_observed_at)
                if retention_prune is not None and retention_prune.changed and retention_cutoff is not None:
                    # A SEPARATE range, not merged into `mutated`. The retention prune deletes the
                    # OLDEST facts while the offered rows are the newest, so a single min..max span
                    # covering both would invalidate the entire store on every retention append.
                    # Recording the deleted range on its own invalidates exactly the buckets whose
                    # facts are gone. Before this, the append-time prune recorded nothing at all.
                    _record_invalidations(
                        connection,
                        (0.0, float(retention_cutoff)),
                        end_exclusive=True,
                        reason="retention_prune",
                        source_generation=generation,
                        now=float(retention_cutoff),
                    )
                    _retire_unactionable_invalidations(connection)
                if changed_intervals:
                    # Through the one interval-to-slot owner: exactly the published buckets whose
                    # half-open span intersects a changed half-open span, interior included and
                    # exclusive end excluded.
                    _record_invalidations(
                        connection,
                        (0.0, 0.0),
                        reason="fact_mutation",
                        source_generation=generation,
                        now=max(
                            (span[1] if span[1] is not None else span[0])
                            for span in changed_intervals
                        ),
                        slots=_slots_intersecting_intervals(connection, changed_intervals),
                    )
                if mutated:
                    # `created_at` is the observed instant that caused the staleness, not a wall
                    # clock read: this module deliberately takes every timestamp as data so a store
                    # has no ambient clock dependency, and the causing instant is the more useful
                    # value anyway when reconciling a late write against its bucket.
                    # EXACT INSTANTS, not `(min, max)`. Collapsing a batch to a span and
                    # clamping it to the ring horizon anchored on the NEWEST instant, so a sparse
                    # batch carrying one old fact and one far-future fact invalidated the future
                    # end and left the old contradicted bucket serving stale data.
                    _record_invalidations(
                        connection,
                        (0.0, 0.0),
                        reason="fact_mutation",
                        source_generation=generation,
                        now=max(mutated),
                        instants=mutated,
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
            retention_cutoff,
            (
                None
                if retention_prune is None
                # `replace`, not a field-by-field rebuild. The only thing this restamp changes is
                # the generation; listing every other field by hand silently dropped
                # `retired_invalidations_deleted` here and reported zero deletions for a pass that
                # really had deleted rows. A copy that enumerates its own fields is a copy that
                # goes stale the next time one is added.
                else replace(retention_prune, source_generation=generation)
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
        read_window: tuple[int | float, int | float] | None = None,
    ) -> StoreSnapshot:
        """Read overlapping coverage plus bounded-history or dirty-window original facts."""

        with self.pinned_snapshot(
            dirty_intervals=dirty_intervals,
            include_coverage=include_coverage,
            read_window=read_window,
        ) as read:
            return read()

    def read_migration_state(
        self,
    ) -> tuple[SchemaMetadata, tuple[MigrationReconciliation, ...]]:
        """Read only the current writer header and durable migration record."""

        connection = self._connection()
        with _transaction(connection):
            header = _read_header(connection)
            reconciliations = _read_migration_reconciliations(connection)
        return _schema_metadata(header), reconciliations

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
    def pinned_snapshot_batches(
        self,
        *,
        read_window: tuple[int | float, int | float] | None = None,
        max_rows: int = REBUILD_BATCH_MAX_ROWS,
        max_decoded_bytes: int = REBUILD_BATCH_MAX_DECODED_BYTES,
        max_memory_bytes: int = REBUILD_MAX_MEMORY_BYTES,
        max_seconds: float = REBUILD_MAX_SECONDS,
        now: Callable[[], float] = time.monotonic,
        memory: Callable[[], int] | None = None,
    ) -> Iterator[Callable[[], Iterator[tuple[Observation, ...]]]]:
        """Pin one WAL generation and stream its observations as bounded decoded batches.

        The sibling of `pinned_snapshot`, for the full rebuild only. `pinned_snapshot` builds one
        `StoreSnapshot` whose `observations` tuple holds every decoded fact, and it builds that
        tuple from a `fetchall()` of every raw row -- so the raw SQLite tuples and the decoded
        `Observation` objects for the WHOLE store are live simultaneously. That is the measured
        880 MiB, and it is the thing this method exists to make structurally impossible.

        **Nothing here holds two representations of the same fact at once.** Rows arrive
        `_REBUILD_FETCH_ROWS` at a time, are decoded, and the raw list is cleared before the next
        fetch, so the peak is one fetch of raw tuples plus one batch of decoded facts -- never one
        store of each. `test_a_batch_never_holds_raw_rows_and_decoded_facts_together` proves it by
        weak reference rather than by asserting a peak number, because a peak assertion passes or
        fails on the chunk size of the day while the reachability claim is the actual invariant.

        Ordering is `observed_at, family, source_id, event_id`, a refinement of `pinned_snapshot`'s
        order by the primary key's last column, which makes it a strict total order so a keyset
        cursor is exact. Paging is keyset, never `OFFSET`, so cost per batch stays constant instead
        of growing with how far in the scan has reached.

        **Scoped to the full rebuild.** There is no `dirty_intervals` parameter on purpose: the
        multi-clause dirty path concatenates one ordered run per interval, so the stream is ordered
        within a run and not across runs, and the open-cell bound a caller would rely on is void.
        The incremental build has no memory problem to solve -- fifty consecutive ones moved traced
        memory 78.15 to 78.55 MiB. Use `pinned_snapshot` there.

        `max_rows` and `max_decoded_bytes` end a BATCH; whichever trips first. `max_memory_bytes`
        and `max_seconds` end the REBUILD, by raising `RebuildBoundExceeded` with a reason code.
        """

        connection = self._connection()
        window = _read_window(read_window)
        time_clause = "" if window is None else " WHERE observed_at >= ?"
        time_parameters: tuple[float, ...] = () if window is None else (float(window[0]),)
        with _transaction(connection):
            _read_header(connection)
            started = now()
            # Read once, here: the bound is growth from the moment the rebuild began, so a
            # baseline taken per-check would always measure zero.
            memory_baseline = (memory or _rebuild_memory_bytes)()

            def read() -> Iterator[tuple[Observation, ...]]:
                cursor_key: tuple[float, str, str, str] | None = None
                while True:
                    if cursor_key is None:
                        sql = (
                            "SELECT event_id, family, source_id, observed_at, epoch_id, "
                            "owner_generation, payload_json FROM observations" + time_clause
                            + " ORDER BY observed_at, family, source_id, event_id"
                        )
                        parameters: tuple[object, ...] = time_parameters
                    else:
                        # Keyset, never OFFSET: SQLite would re-walk the prefix on every page.
                        observed_at, family, source_id, event_id = cursor_key
                        sql = (
                            "SELECT event_id, family, source_id, observed_at, epoch_id, "
                            "owner_generation, payload_json FROM observations WHERE "
                            "(observed_at, family, source_id, event_id) > (?, ?, ?, ?)"
                            + ("" if window is None else " AND observed_at >= ?")
                            + " ORDER BY observed_at, family, source_id, event_id"
                        )
                        parameters = (observed_at, family, source_id, event_id, *time_parameters)
                    cursor = connection.execute(sql, parameters)
                    decoded: list[Observation] = []
                    decoded_bytes = 0
                    while len(decoded) < max_rows and decoded_bytes < max_decoded_bytes:
                        _require_rebuild_within_bounds(
                            started, now, max_memory_bytes, max_seconds, memory_baseline, memory,
                        )
                        # Never fetch past the row bound: one fetch is 2,000 rows, so asking for a
                        # full fetch when 40 rows of budget remain would overshoot by 1,960.
                        raw = cursor.fetchmany(min(_REBUILD_FETCH_ROWS, max_rows - len(decoded)))
                        if not raw:
                            break
                        for row in raw:
                            decoded.append(
                                Observation(
                                    str(row[0]), str(row[1]), str(row[2]), float(row[3]),
                                    str(row[4]), int(row[5]),
                                    _decode_json_object(row[6], "observation payload"),
                                )
                            )
                            decoded_bytes += _payload_byte_length(row[6])
                        last = raw[-1]
                        cursor_key = (float(last[3]), str(last[1]), str(last[2]), str(last[0]))
                        # Before the next fetch, and before the yield: the raw tuples for this
                        # fetch stop being reachable while their decoded facts are still being
                        # accumulated. This `del` is the invariant, not a tidy-up.
                        #
                        # `row` is in it because a `for` loop variable outlives its loop, so
                        # dropping only the list left the LAST row of every fetch reachable from
                        # this frame. One row is not a memory problem; a stated invariant that is
                        # actually false is, and the reachability test caught it here.
                        del raw, last, row
                        # After the fetch, not only before the next one. The `while` condition can
                        # end this batch right here -- `decoded_bytes` may have just crossed
                        # `max_decoded_bytes` -- and control then goes straight to the yield, so a
                        # check that runs only at the top of the loop hands the caller a batch whose
                        # growth was never measured once. Nothing caps `payload_json`, so one fetch
                        # of 2,000 rows is unbounded above; this is the check that makes "no batch
                        # is yielded unmeasured" true on every path rather than on most of them.
                        _require_rebuild_within_bounds(
                            started, now, max_memory_bytes, max_seconds, memory_baseline, memory,
                        )
                    cursor.close()
                    if not decoded:
                        return
                    yield tuple(decoded)
                    # The caller has the batch; this generator must not also keep it alive across
                    # the next fetch, or two batches would be live at the hand-off.
                    del decoded

            yield read

    @contextmanager
    def pinned_snapshot(
        self,
        *,
        dirty_intervals: Iterable[tuple[int | float, int | float]] | None = None,
        include_coverage: bool = True,
        read_window: tuple[int | float, int | float] | None = None,
    ) -> Iterator[Callable[[], StoreSnapshot]]:
        """Pin one WAL generation before yielding its potentially longer row scan."""

        connection = self._connection()
        window = _read_window(read_window)
        intervals = _coalesced_dirty_intervals(dirty_intervals)
        time_clauses = (
            ((" WHERE observed_at >= ?", (window[0],)),)
            if intervals is None and window is not None
            else _time_clauses(intervals)
        )
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
                coverage_where = (
                    "" if window is None
                    else "WHERE (ended_at IS NULL OR ended_at > ?) AND started_at < ? "
                )
                coverage_rows = () if not include_coverage else connection.execute(
                    "SELECT family, source_id, epoch_id, started_at, ended_at, "
                    "native_cadence_seconds, owner_generation FROM coverage_epochs "
                    + coverage_where
                    + "ORDER BY started_at, family, source_id, epoch_id",
                    () if window is None else window,
                ).fetchall()
                if window is not None and coverage_rows:
                    # The materializer uses the epoch immediately before the
                    # first overlap to distinguish a real left-edge outage from
                    # a source whose history simply starts inside the window.
                    overlap_sources = {
                        (str(row[0]), str(row[1])) for row in coverage_rows
                    }
                    spanning_sources = {
                        (str(row[0]), str(row[1]))
                        for row in coverage_rows
                        if float(row[3]) <= window[0]
                    }
                    predecessors = tuple(
                        predecessor
                        for family, source_id in sorted(overlap_sources - spanning_sources)
                        for predecessor in connection.execute(
                            _COVERAGE_PREDECESSOR_SQL,
                            (family, source_id, window[0]),
                        ).fetchall()
                    )
                else:
                    predecessors = ()
                if window is not None:
                    bounded_coverage = {
                        (str(row[0]), str(row[1]), str(row[2])): row
                        for row in (*coverage_rows, *predecessors)
                    }
                    coverage_rows = sorted(
                        bounded_coverage.values(),
                        key=lambda row: (float(row[3]), str(row[0]), str(row[1]), str(row[2])),
                    )
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
                unavailable_where = (
                    "" if window is None
                    else "WHERE ended_at > ? AND started_at < ? "
                )
                unavailable_rows = () if not include_coverage else connection.execute(
                    "SELECT family, source_id, epoch_id, started_at, ended_at, "
                    "native_cadence_seconds, reason, owner_generation FROM unavailable_spans "
                    + unavailable_where
                    + "ORDER BY started_at, family, source_id, epoch_id",
                    () if window is None else window,
                ).fetchall()
                return StoreSnapshot(
                    schema=_schema_metadata(header),
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
                    migration_reconciliation=_read_migration_reconciliations(connection),
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
            pruned = _prune_retained_facts(connection, cutoff)
            generation = int(connection.execute(
                "SELECT source_generation FROM schema_meta WHERE singleton = 1"
            ).fetchone()[0])
            if pruned.changed:
                generation += 1
                connection.execute(
                    "UPDATE schema_meta SET source_generation = ? WHERE singleton = 1",
                    (generation,),
                )
                # Same transaction as the deletion. `prune` advanced the generation and recorded
                # NOTHING, so a nightly prune left every published bucket below the cutoff serving
                # totals for facts that no longer exist and nothing ever asked for a rebuild.
                _record_invalidations(
                    connection,
                    (0.0, cutoff),
                    end_exclusive=True,
                    reason="retention_prune",
                    source_generation=generation,
                    now=cutoff,
                )
                # AFTER recording, in the same transaction. Ordering matters: a real contradiction
                # recorded above for a still-populated slot must be inserted before this sweep can
                # consider anything, and this only ever removes rows whose slot no longer exists.
                _retire_unactionable_invalidations(connection)
        # Recorded only after the transaction commits: a prune that failed must
        # stay due, or one bad night silently becomes a skipped day.
        self._record_pruned_at(now)
        # Same restamp as the append path, through the same one mechanism.
        return replace(pruned, source_generation=generation)


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


def _prune_retired_invalidations(connection: sqlite3.Connection, cutoff: float) -> int:
    """Delete at most one bounded pass of RETIRED ledger rows older than cutoff.

    `applied_at`, never `created_at`. They are different clocks and the schema comment on the table
    says so: `created_at` is the OBSERVED instant of the fact that caused the staleness -- data
    time, which on a store replaying history runs ahead of or behind the wall clock at will --
    while `applied_at` is the publication's wall clock. Retention is a wall-clock policy, so
    `applied_at` is the only column that can express it. `applied_at IS NOT NULL` additionally
    means the row is settled: a PENDING row is outstanding work at any age and must survive every
    cutoff, which is why no age alone is sufficient here.

    Nothing reads a retired row. Every reader of this table -- `pending_invalidation_cells`,
    `_retire_unactionable_invalidations`, the displaced and honest-gap deletes, the publication's
    own retirement, and `read_ring_window`'s stale-bucket set -- filters `applied_at IS NULL`, and
    the table's only index is partial on exactly that predicate. So a retired row is a record of
    completed work that no code path can consult, and the facts it shadows are already deleted at
    the same cutoff. Before this it was the one derived table that outlived its source: measured
    529,217 rows spanning 151.39 h against 45.87 h of `observations`.

    Bounded per pass because this runs inside the service work lock every PRUNE_CHECK_SECONDS. The
    table is WITHOUT ROWID, so the usual `DELETE ... WHERE rowid IN (SELECT ... LIMIT n)` form is
    unavailable and the primary-key triple is selected instead. The DELETE restates the predicate
    rather than trusting the addresses alone, so a row that stopped qualifying cannot be removed by
    an address read earlier in the same statement batch.
    """

    if not _aggregate_tables(connection):
        return 0
    addresses = connection.execute(
        "SELECT resolution_seconds, bucket_start, source_generation FROM ring_invalidations "
        "WHERE applied_at IS NOT NULL AND applied_at < ? LIMIT ?",
        (cutoff, RETIRED_INVALIDATION_PRUNE_LIMIT),
    ).fetchall()
    if not addresses:
        return 0
    connection.executemany(
        "DELETE FROM ring_invalidations WHERE resolution_seconds = ? AND bucket_start = ? "
        "AND source_generation = ? AND applied_at IS NOT NULL AND applied_at < ?",
        [(int(row[0]), int(row[1]), int(row[2]), cutoff) for row in addresses],
    )
    return len(addresses)


def _prune_retained_facts(connection: sqlite3.Connection, cutoff: float) -> PruneResult:
    """Delete or clip every retained fact before cutoff on the caller's transaction.

    THE ONE RETENTION OWNER. Both the explicit `Store.prune` and the append-time retention path
    call this, so a delete added here is enforced by both and a table added to the schema without
    one here is the only way a shadow can outlive its facts again. Do not add a second scheduler.
    """

    _prune_browser_diagnostics(connection, cutoff)
    observations = connection.execute(
        "DELETE FROM observations WHERE observed_at < ?", (cutoff,)
    ).rowcount
    usage_atoms = connection.execute(
        "DELETE FROM usage_atoms WHERE observed_at < ?", (cutoff,)
    ).rowcount
    coverage_deleted = connection.execute(
        "DELETE FROM coverage_epochs WHERE ended_at IS NOT NULL AND ended_at <= ?",
        (cutoff,),
    ).rowcount
    coverage_clipped = connection.execute(
        "UPDATE coverage_epochs SET started_at = ? "
        "WHERE started_at < ? AND (ended_at IS NULL OR ended_at > ?)",
        (cutoff, cutoff, cutoff),
    ).rowcount
    unavailable_deleted = connection.execute(
        "DELETE FROM unavailable_spans WHERE ended_at <= ?", (cutoff,)
    ).rowcount
    unavailable_clipped = connection.execute(
        "UPDATE unavailable_spans SET started_at = ? "
        "WHERE started_at < ? AND ended_at > ?",
        (cutoff, cutoff, cutoff),
    ).rowcount
    # Last, and reported separately from every counter above it. The rows removed here are the
    # ledger's record of completed work, not facts, so they are deliberately absent from
    # `PruneResult.changed` and must stay absent -- see that property.
    retired_invalidations = _prune_retired_invalidations(connection, cutoff)
    return PruneResult(
        observations,
        coverage_deleted,
        coverage_clipped,
        usage_atoms,
        0,
        unavailable_deleted,
        unavailable_clipped,
        retired_invalidations,
    )


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
