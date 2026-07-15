"""Pure YO!stats bucket semantics and the single-writer SQLite/WAL store.

The web application historically held these dictionaries, their compaction
rules, and a full JSON snapshot in every listener.  The pure helpers here are
deliberately independent of HTTP/tmux state so ``statsd`` can be the one durable
owner while old callers retain the exact bucket wire shape during migration.
"""

from __future__ import annotations

import copy
import json
import math
import sqlite3
import time
from pathlib import Path
from typing import Any

from .. import stats_families


STATS_STORE_SCHEMA_VERSION = 4
STATS_STORE_MAX_JSON_BYTES = 256 * 1024
STATS_STORE_MAX_ROWS_PER_QUERY = 20_000
STATS_COST_SUMMARY_MAX_COMPONENTS = 4096
STATS_COST_SUMMARY_MAX_BYTES = 160 * 1024
STATS_COVERAGE_MAX_INTERVALS = 128
# Family facts (names, cadences, storage fields) have ONE owner: the frozen
# manifest in yolomux_lib/stats_families.py. This module only derives.
STATS_COVERAGE_FAMILIES = stats_families.STATS_COVERAGE_FAMILY_NAMES
STATS_COVERAGE_LEGACY_CADENCE = stats_families.STATS_COVERAGE_LEGACY_CADENCE

BROWSER_FIELDS = (
    "api_count",
    "sse_count",
    "latency_total_ms",
    "latency_count",
    "bandwidth_bytes",
    "heartbeat_count",
    "disconnected_ms",
)
PROCESS_FIELDS = ("cpu_total_percent", "cpu_count")
SERVER_FIELDS = stats_families.SERVER_BUCKET_FIELDS


def empty_host_metrics() -> dict[str, Any]:
    return {
        name: "" if kind == "label" else 0.0 if kind == "sum" else {}
        for name, kind in stats_families.HOST_METRIC_FIELDS
    }


def empty_client_bucket() -> dict[str, Any]:
    return {
        "sequence": 0,
        "api_count": 0.0,
        "sse_count": 0.0,
        "latency_total_ms": 0.0,
        "latency_count": 0.0,
        "bandwidth_bytes": 0.0,
        "heartbeat_count": 0.0,
        "disconnected_ms": 0.0,
    }


def empty_process_bucket() -> dict[str, Any]:
    return {
        "sequence": 0,
        "label": "",
        "pid": 0,
        "port": 0,
        "started_at": 0.0,
        "cpu_total_percent": 0.0,
        "cpu_count": 0.0,
    }


def empty_bucket(start: int, duration: int) -> dict[str, Any]:
    return {
        "start": int(start),
        "duration": max(1, int(duration)),
        "sequence": 0,
        "server_sequence": 0,
        **{field: 0.0 for field in SERVER_FIELDS},
        "agent_token_rates": {},
        # Component-level model usage is intentionally retained beside the
        # compatibility output-token projection.  It remains JSON because the
        # grouping dimensions evolve with provider billing schemas; statsd is
        # its single writer and bounds the list before persistence.
        "cost_summary": {"components": [], "total_micro_usd": 0, "priced_components": 0, "unpriced_components": 0, "lower_bound": False},
        "host_metrics": empty_host_metrics(),
        "clients": {},
        "servers": {},
    }


def _finite(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) and number >= 0.0 else 0.0


def normalize_bucket(value: Any, *, owned: bool = False) -> dict[str, Any]:
    """Return one bounded, JSON-safe bucket without trusting persisted input.

    ``owned=True`` marks a bucket the caller exclusively owns (for example one
    freshly decoded with ``json.loads``): its nested mappings are adopted by
    reference instead of deep-copied.  The hot read paths decode thousands of
    dense bucket rows per range request, and deep-copying every nested map
    dominated that latency while protecting nothing.
    """
    raw = value if isinstance(value, dict) else {}
    result = empty_bucket(int(_finite(raw.get("start"))), max(1, int(_finite(raw.get("duration")))))
    result["sequence"] = int(_finite(raw.get("sequence")))
    result["server_sequence"] = int(_finite(raw.get("server_sequence")))
    for field in SERVER_FIELDS:
        result[field] = _finite(raw.get(field))
    for mapping_field in ("agent_token_rates", "cost_summary", "host_metrics", "clients", "servers"):
        candidate = raw.get(mapping_field)
        if isinstance(candidate, dict):
            result[mapping_field] = candidate if owned else copy.deepcopy(candidate)
    return result


def _decode_bucket_row(encoded: Any) -> dict[str, Any]:
    """One owner of the bucket_json -> normalized bucket read decode."""
    return normalize_bucket(json.loads(str(encoded)), owned=True)


def merge_bucket(target: dict[str, Any], source: dict[str, Any]) -> None:
    """Add aggregate values while preserving the latest identity/sequence facts."""
    source = normalize_bucket(source)
    for field in SERVER_FIELDS:
        target[field] = _finite(target.get(field)) + _finite(source.get(field))
    target["sequence"] = max(int(target.get("sequence") or 0), int(source.get("sequence") or 0), int(source.get("server_sequence") or 0))
    target["server_sequence"] = max(int(target.get("server_sequence") or 0), int(source.get("server_sequence") or 0))
    for mapping_field in ("agent_token_rates", "host_metrics", "clients", "servers"):
        if not target.get(mapping_field) and source.get(mapping_field):
            target[mapping_field] = copy.deepcopy(source[mapping_field])
    if source.get("cost_summary"):
        # The statsd service performs the dimension-aware merge.  This pure
        # fallback retains data for legacy import callers that only need a
        # lossless bucket round trip.
        if not target.get("cost_summary"):
            target["cost_summary"] = copy.deepcopy(source["cost_summary"])


class StatsStore:
    """SQLite WAL database owned by exactly one ``statsd`` process.

    Schema 4 stores exactly three tables: ``stats_buckets`` (each fact stored
    once, in ``bucket_json``), ``stats_coverage_intervals``, and
    ``schema_meta``.  A read-only peer (the web's in-process
    ``StatsHistoryReader``) never migrates: it reads only those three tables,
    which exist under both the current and the legacy schema, so it serves a
    not-yet-migrated database unchanged and picks up the owner's migration
    transparently (a mid-migration schema change surfaces as one retried
    ``sqlite3.OperationalError`` on its lazy connection).
    """

    def __init__(self, path: Path, *, read_only: bool = False):
        self.path = Path(path)
        self.read_only = bool(read_only)
        self.connection: sqlite3.Connection | None = None

    def open(self) -> None:
        if self.read_only:
            # Read-only WAL peers (the web's in-process StatsHistoryReader)
            # serve HTTP request threads, so the shared connection must not be
            # pinned to its creating thread; every caller serializes use behind
            # its own lock. The open itself can raise OperationalError when the
            # database file does not exist yet (fresh state dir before the
            # statsd owner created it) — callers open lazily and retry.
            self.connection = sqlite3.connect(
                f"file:{self.path}?mode=ro", uri=True, timeout=2.0, check_same_thread=False,
            )
            self.connection.execute("PRAGMA query_only=ON")
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._quarantine_if_corrupt()
        connection = sqlite3.connect(self.path, timeout=2.0)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS stats_buckets (
              start INTEGER NOT NULL, duration INTEGER NOT NULL, sequence INTEGER NOT NULL,
              server_sequence INTEGER NOT NULL, bucket_json TEXT NOT NULL,
              PRIMARY KEY (start, duration)
            );
            CREATE INDEX IF NOT EXISTS stats_buckets_sequence ON stats_buckets(sequence);
            CREATE TABLE IF NOT EXISTS stats_coverage_intervals (
              family TEXT NOT NULL, epoch_id TEXT NOT NULL,
              start INTEGER NOT NULL, end INTEGER NOT NULL,
              cadence INTEGER NOT NULL, owner_generation INTEGER NOT NULL,
              source TEXT NOT NULL DEFAULT 'sampler',
              PRIMARY KEY (family, epoch_id, start)
            );
            CREATE INDEX IF NOT EXISTS stats_coverage_family_range
              ON stats_coverage_intervals(family, start, end);
            -- Original per-family observations (DOIT.1 item 2). One un-aggregated
            -- instant per row, keyed by emitter identity + real sample time — the
            -- durable source the memory materializer folds into exact resolution
            -- layers. Empty until the migration populates it; additive here so a
            -- fresh or already-current DB simply carries empty tables.
            CREATE TABLE IF NOT EXISTS stats_raw_samples (
              family TEXT NOT NULL, source_id TEXT NOT NULL, sample_time REAL NOT NULL,
              epoch_id TEXT NOT NULL, owner_generation INTEGER NOT NULL DEFAULT 0,
              payload_json TEXT NOT NULL,
              PRIMARY KEY (family, source_id, sample_time)
            );
            CREATE INDEX IF NOT EXISTS stats_raw_samples_time ON stats_raw_samples(sample_time);
            -- Event-identity-keyed usage/cost atoms (re-derivable from transcripts),
            -- separate from time-bucketed samples because they dedup by identity.
            CREATE TABLE IF NOT EXISTS stats_usage_atoms (
              event_id TEXT NOT NULL, direction TEXT NOT NULL, modality TEXT NOT NULL,
              cache_role TEXT NOT NULL, unit TEXT NOT NULL, sample_time REAL NOT NULL,
              atom_json TEXT NOT NULL,
              PRIMARY KEY (event_id, direction, modality, cache_role, unit)
            );
            CREATE INDEX IF NOT EXISTS stats_usage_atoms_time ON stats_usage_atoms(sample_time);
            """
        )
        self._migrate_retired_tables(connection)
        connection.execute(
            "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(STATS_STORE_SCHEMA_VERSION),),
        )
        self._repair_coverage_overlaps(connection)
        connection.commit()
        self.connection = connection

    def _quarantine_if_corrupt(self) -> Path | None:
        """Move an unreadable/malformed DB aside instead of crash-looping at open.

        A genuinely corrupt file (truncated, "file is not a database", disk image
        malformed) cannot be opened for schema creation or migration, so the
        writer would fail on every start. Rather than delete it (which would lose
        any forensic value and violate "migrate, do not wipe"), rename it to a
        timestamped `.corrupt-<stamp>` sidecar and let open() create a fresh DB.

        Only genuine corruption is quarantined: a valid empty/new DB, a
        pre-schema DB ("no such table"), and a transiently locked DB all raise
        different errors and are left untouched (re-raised or ignored). The probe
        is a cheap `PRAGMA schema_version` (header check, no full scan), not
        `integrity_check`, so it stays fast on every open. Returns the sidecar
        path when it quarantined, else None.
        """
        if not self.path.exists() or self.path.stat().st_size == 0:
            return None
        try:
            probe = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, timeout=2.0)
            try:
                probe.execute("PRAGMA schema_version").fetchone()
            finally:
                probe.close()
            return None
        except sqlite3.DatabaseError as exc:
            message = str(exc).lower()
            if "not a database" not in message and "malformed" not in message:
                # Locked/busy/other transient error — not corruption; let the
                # real open() handle it (it may retry or surface the error).
                return None
        stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        quarantine = self.path.with_name(f"{self.path.name}.corrupt-{stamp}")
        self.path.rename(quarantine)
        for suffix in ("-wal", "-shm"):
            sidecar = self.path.with_name(f"{self.path.name}{suffix}")
            if sidecar.exists():
                sidecar.rename(quarantine.with_name(f"{quarantine.name}{suffix}"))
        return quarantine

    @staticmethod
    def _migrate_retired_tables(connection: sqlite3.Connection) -> int:
        """One-time in-place migration to the single-owner schema (version 4).

        MEASURED RATIONALE (2026-07 code audit + live database inspection):
        every write path was a full-bucket read-modify-write — the merge paths
        load the whole bucket through ``bucket()`` (``bucket_json``), mutate it
        in memory, and rewrite ``bucket_json`` through ``_upsert_bucket``.  No
        path ever updated a side table without rewriting ``bucket_json``, and
        no read path ever read the side tables back, so the four normalized
        side tables (``stats_clients``/``stats_processes``/``stats_agent_rates``
        /``stats_host_metrics``) were pure write-amplified duplication.
        ``bucket_json`` is therefore the ONE storage owner.  ``stats_rollups``
        was empty on live systems (its serve branch always fell back to the
        graduated ``stats_buckets`` tiers) and its rows are pure projections of
        those buckets, so folding them back would double-count: rollups are
        dropped, while any side-table group missing from its ``bucket_json``
        (none observed live) is folded in before the tables are dropped.
        Idempotent: a second open finds no retired tables and does nothing.
        Returns the number of bucket rows repaired from side-table facts.
        """
        existing = {
            str(row[0]) for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        folded = 0
        for table, field, key_column in (
            ("stats_clients", "clients", "client_id"),
            ("stats_processes", "servers", "process_id"),
            ("stats_agent_rates", "agent_token_rates", "rate_key"),
            ("stats_host_metrics", "host_metrics", "metric_key"),
        ):
            if table not in existing:
                continue
            for start, duration in connection.execute(
                f"SELECT DISTINCT start,duration FROM {table}"
            ).fetchall():
                row = connection.execute(
                    "SELECT bucket_json FROM stats_buckets WHERE start=? AND duration=?",
                    (start, duration),
                ).fetchone()
                if row is None:
                    continue
                try:
                    bucket = json.loads(str(row[0]))
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                if not isinstance(bucket, dict) or bucket.get(field):
                    continue
                values = {}
                for key, values_json in connection.execute(
                    f"SELECT {key_column},values_json FROM {table} WHERE start=? AND duration=?",
                    (start, duration),
                ).fetchall():
                    try:
                        decoded = json.loads(str(values_json))
                    except (TypeError, ValueError, json.JSONDecodeError):
                        continue
                    if isinstance(decoded, dict):
                        values[str(key)] = decoded
                if not values:
                    continue
                bucket[field] = values
                try:
                    encoded = StatsStore._encode(normalize_bucket(bucket))
                except ValueError:
                    continue
                connection.execute(
                    "UPDATE stats_buckets SET bucket_json=? WHERE start=? AND duration=?",
                    (encoded, start, duration),
                )
                folded += 1
            connection.execute(f"DROP TABLE {table}")
        if "stats_rollups" in existing:
            connection.execute("DROP TABLE stats_rollups")
        return folded

    @staticmethod
    def _coverage_overlap_pairs(connection: sqlite3.Connection) -> int:
        """One owner of the coverage-overlap definition (a self-join count).

        Shared by the durable-repair gate and the integrity self-check so the
        meaning of "overlap" lives in exactly one place.
        """
        return int(connection.execute(
            "SELECT COUNT(*) FROM stats_coverage_intervals a "
            "JOIN stats_coverage_intervals b ON a.family=b.family AND a.rowid<b.rowid "
            "AND a.end > b.start AND b.end > a.start"
        ).fetchone()[0])

    @staticmethod
    def _clip_disjoint_intervals(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """One owner of the seam rule: clip a start-sorted list to disjoint.

        Where two consecutive intervals overlap the later owner is
        authoritative, so the earlier interval's ``end`` is clipped to the
        later interval's ``start`` and any interval that collapses to
        zero/negative width is dropped.  Mutates and returns the surviving
        dicts, each keeping its carry-through keys (``epoch_id``, ``rowid`` …),
        so both the serve path and the durable repair share this rule.
        """
        disjoint: list[dict[str, Any]] = []
        for item in items:
            if disjoint and int(disjoint[-1]["end"]) > int(item["start"]):
                disjoint[-1]["end"] = int(item["start"])
            if disjoint and int(disjoint[-1]["end"]) <= int(disjoint[-1]["start"]):
                disjoint.pop()
            if int(item["end"]) <= int(item["start"]):
                continue
            disjoint.append(item)
        return disjoint

    @staticmethod
    def _repair_coverage_overlaps(connection: sqlite3.Connection) -> int:
        """Clip any pre-existing cross-epoch overlaps durably (idempotent).

        Databases written before write-time handoff clipping can hold boundary
        overlaps from statsd restarts.  Detection is a cheap self-join; when it
        finds none this is a no-op, so it is safe to run on every open.  Repair
        applies the shared serve-time seam rule (``_clip_disjoint_intervals``)
        to each family's rows and writes back only the differences.
        """

        if StatsStore._coverage_overlap_pairs(connection) == 0:
            return 0
        repaired = 0
        families = [row[0] for row in connection.execute(
            "SELECT DISTINCT family FROM stats_coverage_intervals"
        ).fetchall()]
        for family in families:
            rows = [
                {"rowid": row[0], "start": int(row[1]), "end": int(row[2])}
                for row in connection.execute(
                    "SELECT rowid,start,end FROM stats_coverage_intervals WHERE family=? ORDER BY start,end",
                    (family,),
                ).fetchall()
            ]
            survivors = {
                item["rowid"]: item
                for item in StatsStore._clip_disjoint_intervals([dict(row) for row in rows])
            }
            for row in rows:
                survivor = survivors.get(row["rowid"])
                if survivor is None:
                    connection.execute("DELETE FROM stats_coverage_intervals WHERE rowid=?", (row["rowid"],))
                    repaired += 1
                elif int(survivor["end"]) != row["end"]:
                    connection.execute("UPDATE stats_coverage_intervals SET end=? WHERE rowid=?", (int(survivor["end"]), row["rowid"]))
                    repaired += 1
        return repaired

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def _connection(self) -> sqlite3.Connection:
        if self.connection is None:
            self.open()
        assert self.connection is not None
        return self.connection

    @staticmethod
    def _encode(value: dict[str, Any]) -> str:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > STATS_STORE_MAX_JSON_BYTES:
            raise ValueError("stats bucket is too large")
        return encoded

    def _upsert_bucket(self, connection: sqlite3.Connection, bucket: dict[str, Any]) -> None:
        # ``bucket_json`` is the single storage owner: every field group
        # (clients, servers, agent_token_rates, host_metrics, cost_summary)
        # is stored exactly once, here.  See ``_migrate_retired_tables`` for
        # the measured rationale that retired the duplicated side tables.
        normalized = normalize_bucket(bucket)
        connection.execute(
            "INSERT INTO stats_buckets(start,duration,sequence,server_sequence,bucket_json) VALUES(?,?,?,?,?) "
            "ON CONFLICT(start,duration) DO UPDATE SET sequence=excluded.sequence,server_sequence=excluded.server_sequence,bucket_json=excluded.bucket_json",
            (normalized["start"], normalized["duration"], normalized["sequence"], normalized["server_sequence"], self._encode(normalized)),
        )

    def upsert_bucket(self, bucket: dict[str, Any]) -> None:
        connection = self._connection()
        with connection:
            self._upsert_bucket(connection, bucket)

    def record_sample_coverage(
        self,
        *,
        family: str,
        sample_time: float,
        cadence: float,
        epoch_id: str,
        owner_generation: int = 0,
    ) -> None:
        """Extend one sampler epoch without ever bridging a real epoch boundary."""

        if self.read_only:
            raise RuntimeError("read-only stats store cannot record coverage")
        family = str(family or "").strip()
        epoch_id = str(epoch_id or "").strip()
        if family not in STATS_COVERAGE_FAMILIES or not epoch_id:
            raise ValueError("invalid stats coverage sample")
        start = max(0, int(math.floor(float(sample_time))))
        cadence_seconds = max(1, int(math.ceil(float(cadence))))
        end = start + cadence_seconds
        generation = max(0, int(owner_generation))
        connection = self._connection()
        with connection:
            self._record_sample_coverage(
                connection,
                family=family,
                start=start,
                end=end,
                cadence=cadence_seconds,
                epoch_id=epoch_id,
                owner_generation=generation,
            )

    @staticmethod
    def _record_sample_coverage(
        connection: sqlite3.Connection,
        *,
        family: str,
        start: int,
        end: int,
        cadence: int,
        epoch_id: str,
        owner_generation: int,
    ) -> None:
        row = connection.execute(
            "SELECT start,end,cadence FROM stats_coverage_intervals "
            "WHERE family=? AND epoch_id=? ORDER BY end DESC LIMIT 1",
            (family, epoch_id),
        ).fetchone()
        if row is not None and start <= int(row[1]) + cadence:
            connection.execute(
                "UPDATE stats_coverage_intervals SET end=?,cadence=?,owner_generation=? "
                "WHERE family=? AND epoch_id=? AND start=?",
                (max(end, int(row[1])), cadence, owner_generation, family, epoch_id, int(row[0])),
            )
        else:
            # A new interval (owner handoff or an in-epoch gap) authoritatively
            # ends any earlier same-family interval that still extends past this
            # start, so a restarting owner can never persist a boundary overlap.
            connection.execute(
                "UPDATE stats_coverage_intervals SET end=? WHERE family=? AND start<? AND end>?",
                (start, family, start, start),
            )
            connection.execute(
                "DELETE FROM stats_coverage_intervals WHERE family=? AND end<=start",
                (family,),
            )
            connection.execute(
                "INSERT OR REPLACE INTO stats_coverage_intervals"
                "(family,epoch_id,start,end,cadence,owner_generation,source) VALUES(?,?,?,?,?,?,'sampler')",
                (family, epoch_id, start, end, cadence, owner_generation),
            )

    def upsert_bucket_with_coverage(
        self,
        bucket: dict[str, Any],
        coverage_samples: list[dict[str, Any]],
    ) -> None:
        """Commit one bucket revision and all of its coverage facts atomically."""

        connection = self._connection()
        with connection:
            self._upsert_bucket(connection, bucket)
            for sample in coverage_samples:
                self._record_sample_coverage(connection, **sample)

    def bucket(self, start: int, duration: int) -> dict[str, Any] | None:
        row = self._connection().execute(
            "SELECT bucket_json FROM stats_buckets WHERE start=? AND duration=?",
            (int(start), int(duration)),
        ).fetchone()
        return _decode_bucket_row(row[0]) if row is not None else None

    def replace_buckets(self, buckets: list[dict[str, Any]], *, preserve_coverage: bool = False) -> None:
        """Atomically replace the durable history after compaction/import."""
        connection = self._connection()
        normalized = [normalize_bucket(bucket) for bucket in buckets]
        with connection:
            tables = ["stats_buckets"]
            if not preserve_coverage:
                tables.append("stats_coverage_intervals")
            for table in tables:
                connection.execute(f"DELETE FROM {table}")
            for bucket in normalized:
                self._upsert_bucket(connection, bucket)

    def replace_buckets_with_marker(self, buckets: list[dict[str, Any]], marker_key: str, marker_value: str) -> None:
        """Atomically replace durable history and record the completed migration."""
        connection = self._connection()
        normalized = [normalize_bucket(bucket) for bucket in buckets]
        with connection:
            for table in ("stats_buckets", "stats_coverage_intervals"):
                connection.execute(f"DELETE FROM {table}")
            for bucket in normalized:
                self._upsert_bucket(connection, bucket)
            connection.execute(
                "INSERT INTO schema_meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(marker_key), str(marker_value)),
            )

    def vacuum(self) -> dict[str, int]:
        """Reclaim freed pages to the OS via a full VACUUM.

        Retention/compaction delete rows but SQLite only marks their pages free
        (auto_vacuum is off), so the file never shrinks on its own — a live DB
        observed at 210 MB was 85% freelist over ~32 MB of real data. A full
        VACUUM rebuilds the file at its true size; it is fast on the small live
        set. VACUUM cannot run inside a transaction, so it uses the connection
        directly with no open `with connection:` block. Returns before/after
        byte sizes for diagnostics.

        In WAL mode VACUUM rebuilds the database into the WAL; the main file is
        NOT truncated until a checkpoint, so a plain VACUUM leaves the on-disk
        size unchanged (measured: 32 MB stays 32 MB). A TRUNCATE checkpoint after
        the VACUUM applies the rebuilt pages and shrinks the file to its true
        size (measured: 32 MB -> 8 KB). WAL mode is preserved across both.
        """
        connection = self._connection()
        before = self.path.stat().st_size if self.path.exists() else 0
        connection.execute("VACUUM")
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        after = self.path.stat().st_size if self.path.exists() else 0
        return {"bytes_before": int(before), "bytes_after": int(after)}

    def metadata_value(self, key: str) -> str | None:
        row = self._connection().execute("SELECT value FROM schema_meta WHERE key=?", (str(key),)).fetchone()
        return str(row[0]) if row is not None else None

    def set_metadata_value(self, key: str, value: str) -> None:
        connection = self._connection()
        with connection:
            connection.execute(
                "INSERT INTO schema_meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(key), str(value)),
            )

    def query_buckets(
        self,
        *,
        after_sequence: int = 0,
        start: int = 0,
        end: int = 0,
        limit: int = STATS_STORE_MAX_ROWS_PER_QUERY,
    ) -> list[dict[str, Any]]:
        clauses = ["sequence > ?"]
        values: list[Any] = [max(0, int(after_sequence))]
        if start:
            clauses.append("start + duration > ?")
            values.append(max(0, int(start)))
        if end:
            clauses.append("start < ?")
            values.append(max(0, int(end)))
        values.append(max(1, min(int(limit), STATS_STORE_MAX_ROWS_PER_QUERY)))
        rows = self._connection().execute(
            f"SELECT bucket_json FROM stats_buckets WHERE {' AND '.join(clauses)} ORDER BY start,duration LIMIT ?", values
        ).fetchall()
        return [_decode_bucket_row(row[0]) for row in rows]

    def all_buckets(self) -> list[dict[str, Any]]:
        """Return every retained bucket for single-writer maintenance.

        Public/history reads remain capped by ``query_buckets``.  Maintenance
        paths which replace or reproject the durable store must never use that
        response cap, otherwise they can silently discard the tail of a large
        legacy import when writing their replacement snapshot.
        """
        rows = self._connection().execute(
            "SELECT bucket_json FROM stats_buckets ORDER BY start,duration"
        ).fetchall()
        return [_decode_bucket_row(row[0]) for row in rows]

    def maintenance_buckets_after(
        self, *, after_start: int = -1, after_duration: int = -1, limit: int = 1
    ) -> list[dict[str, Any]]:
        """Read one keyset-bounded maintenance page in durable order.

        Maintenance must visit all retained rows, but it must not materialize
        them all and monopolize statsd's single RPC/SQLite owner at startup.
        """
        rows = self._connection().execute(
            "SELECT bucket_json FROM stats_buckets "
            "WHERE start > ? OR (start = ? AND duration > ?) "
            "ORDER BY start,duration LIMIT ?",
            (int(after_start), int(after_start), int(after_duration), max(1, min(int(limit), STATS_STORE_MAX_ROWS_PER_QUERY))),
        ).fetchall()
        return [_decode_bucket_row(row[0]) for row in rows]

    def retention_candidate(
        self, *, now: float, retention_seconds: int, tiers: tuple[tuple[int, int], ...]
    ) -> dict[str, Any] | None:
        """Return one row that is expired or finer than its current age tier."""

        clauses = ["start <= 0", "start < ?"]
        values: list[Any] = [float(now) - int(retention_seconds)]
        previous_age = 0
        for max_age, bucket_seconds in tiers:
            clauses.append("(start >= ? AND start < ? AND duration < ?)")
            values.extend((float(now) - int(max_age), float(now) - previous_age, int(bucket_seconds)))
            previous_age = int(max_age)
        row = self._connection().execute(
            f"SELECT bucket_json FROM stats_buckets WHERE {' OR '.join(clauses)} ORDER BY start,duration LIMIT 1",
            values,
        ).fetchone()
        return _decode_bucket_row(row[0]) if row is not None else None

    def replace_compacted_bucket(
        self, source_start: int, source_duration: int, replacement: dict[str, Any] | None
    ) -> None:
        """Atomically retire one source row and optionally merge its replacement."""

        connection = self._connection()
        with connection:
            connection.execute(
                "DELETE FROM stats_buckets WHERE start=? AND duration=?",
                (int(source_start), int(source_duration)),
            )
            if replacement is not None:
                self._upsert_bucket(connection, replacement)

    def latest_sequence(self) -> int:
        row = self._connection().execute("SELECT COALESCE(MAX(sequence), 0) FROM stats_buckets").fetchone()
        return int(row[0] or 0)

    @staticmethod
    def _legacy_bucket_has_family(bucket: dict[str, Any], family: str) -> bool:
        if family == "raw":
            return True
        if family == "cpu":
            return bool(float(bucket.get("cpu_count") or 0) or float(bucket.get("system_cpu_count") or 0))
        if family == "agent_status":
            return bool(float(bucket.get("agent_activity_samples") or 0))
        if family == "agent_tokens":
            return bool(
                float(bucket.get("agent_token_samples") or 0)
                or bucket.get("agent_token_rates")
            )
        if family == "cost":
            summary = bucket.get("cost_summary") if isinstance(bucket.get("cost_summary"), dict) else {}
            return bool(summary.get("components") or int(summary.get("priced_components") or 0) or int(summary.get("unpriced_components") or 0))
        metrics = bucket.get("host_metrics") if isinstance(bucket.get("host_metrics"), dict) else {}
        if family == "gpu":
            return bool(metrics.get("gpu_devices") or metrics.get("gpu_util_processes") or metrics.get("gpu_memory_processes"))
        if family == "system_memory":
            return bool(float(metrics.get("system_memory_count") or 0))
        return False

    @staticmethod
    def _bounded_intervals(intervals: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
        """Sort/coalesce within an epoch, clip cross-epoch overlaps, and bound.

        Coalescing joins same-epoch adjacency but keeps distinct sampler epochs
        as separate intervals so per-owner identity survives for diagnostics.
        A statsd restart/owner handoff can still leave the previous owner's
        finalized ``end`` a few seconds past the new owner's backfilled
        ``start`` (the old owner's last flush races the new owner opening its
        interval).  Those boundary overlaps must never reach the client, which
        requires a strictly disjoint, sorted interval list: the later owner is
        authoritative at the seam, so the earlier interval's ``end`` is clipped
        down to the later interval's ``start`` and any interval that collapses
        to zero/negative width is dropped.
        """

        merged: list[dict[str, Any]] = []
        for raw in sorted(intervals, key=lambda item: (int(item["start"]), int(item["end"]), str(item["epoch_id"]))):
            item = dict(raw)
            if merged and merged[-1]["epoch_id"] == item["epoch_id"] and int(item["start"]) <= int(merged[-1]["end"]):
                merged[-1]["end"] = max(int(merged[-1]["end"]), int(item["end"]))
                continue
            merged.append(item)
        disjoint = StatsStore._clip_disjoint_intervals(merged)
        truncated = len(disjoint) > STATS_COVERAGE_MAX_INTERVALS
        return (disjoint[-STATS_COVERAGE_MAX_INTERVALS:] if truncated else disjoint), truncated

    def _legacy_coverage_intervals(
        self,
        buckets: list[dict[str, Any]],
        family: str,
        *,
        start: int,
        end: int,
        before: int = 0,
    ) -> list[dict[str, Any]]:
        cadence = STATS_COVERAGE_LEGACY_CADENCE[family]
        result: list[dict[str, Any]] = []
        epoch_number = 0
        previous_end = 0
        for bucket in buckets:
            if not self._legacy_bucket_has_family(bucket, family):
                continue
            interval_start = max(start, int(bucket["start"])) if start else int(bucket["start"])
            interval_end = int(bucket["start"]) + max(1, int(bucket["duration"]))
            if before and interval_start >= before:
                continue
            interval_end = min(interval_end, before) if before else interval_end
            interval_end = min(interval_end, end) if end else interval_end
            if interval_end <= interval_start:
                continue
            if not result or interval_start > previous_end + cadence:
                epoch_number += 1
                result.append({
                    "start": interval_start,
                    "end": interval_end,
                    "epoch_id": f"legacy:{family}:{epoch_number}",
                    "owner_generation": 0,
                    "source": "legacy-derived",
                })
            else:
                result[-1]["end"] = max(int(result[-1]["end"]), interval_end)
            previous_end = max(previous_end, interval_end)
        return result

    def query_coverage(self, *, start: int = 0, end: int = 0) -> dict[str, Any]:
        """Return bounded multi-interval and per-store coverage facts.

        Explicit sampler epochs are authoritative for new writes.  Old schema
        databases remain readable: their pre-epoch prefix is derived from real
        metric presence and split whenever a cadence-sized outage occurs.
        """
        clauses: list[str] = []
        values: list[int] = []
        if start:
            clauses.append("start + duration > ?")
            values.append(max(0, int(start)))
        if end:
            clauses.append("start < ?")
            values.append(max(0, int(end)))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        connection = self._connection()
        row = connection.execute(
            f"SELECT COALESCE(MIN(start), 0), COALESCE(MAX(start + duration), 0), COALESCE(MAX(duration), 0), COUNT(*) FROM stats_buckets {where}",
            values,
        ).fetchone()
        # Coverage needs only presence fields. Avoid normalize/deep-copy of
        # every nested agent/cost/process map on this latency-sensitive reader
        # path; SQLite/WAL remains the source and malformed legacy JSON is
        # treated as an uncovered row.
        bucket_rows = connection.execute(
            f"SELECT bucket_json FROM stats_buckets {where} ORDER BY start,duration LIMIT ?",
            (*values, STATS_STORE_MAX_ROWS_PER_QUERY),
        ).fetchall()
        buckets: list[dict[str, Any]] = []
        for bucket_row in bucket_rows:
            try:
                decoded = json.loads(str(bucket_row[0]))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if isinstance(decoded, dict):
                buckets.append(decoded)
        explicit_clauses: list[str] = []
        explicit_values: list[int] = []
        if start:
            explicit_clauses.append("end > ?")
            explicit_values.append(max(0, int(start)))
        if end:
            explicit_clauses.append("start < ?")
            explicit_values.append(max(0, int(end)))
        explicit_where = f"WHERE {' AND '.join(explicit_clauses)}" if explicit_clauses else ""
        has_epoch_table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='stats_coverage_intervals'"
        ).fetchone() is not None
        explicit_rows = connection.execute(
            "SELECT family,epoch_id,start,end,cadence,owner_generation,source "
            f"FROM stats_coverage_intervals {explicit_where} ORDER BY family,start,end",
            explicit_values,
        ).fetchall() if has_epoch_table else []
        by_family: dict[str, list[dict[str, Any]]] = {family: [] for family in STATS_COVERAGE_FAMILIES}
        first_explicit: dict[str, int] = {}
        for family, epoch_id, item_start, item_end, _cadence, generation, source in explicit_rows:
            family = str(family)
            if family not in by_family:
                continue
            clipped_start = max(int(item_start), int(start)) if start else int(item_start)
            clipped_end = min(int(item_end), int(end)) if end else int(item_end)
            if clipped_end <= clipped_start:
                continue
            first_explicit[family] = min(first_explicit.get(family, clipped_start), clipped_start)
            by_family[family].append({
                "start": clipped_start,
                "end": clipped_end,
                "epoch_id": str(epoch_id),
                "owner_generation": int(generation),
                "source": str(source),
            })
        stores: dict[str, dict[str, Any]] = {}
        for family in STATS_COVERAGE_FAMILIES:
            legacy = self._legacy_coverage_intervals(
                buckets,
                family,
                start=start,
                end=end,
                before=first_explicit.get(family, 0),
            )
            intervals, truncated = self._bounded_intervals(legacy + by_family[family])
            requested_start = int(start) if start else (int(intervals[0]["start"]) if intervals else 0)
            requested_end = int(end) if end else (int(intervals[-1]["end"]) if intervals else 0)
            cursor = requested_start
            for interval in intervals:
                if int(interval["end"]) <= cursor:
                    continue
                if int(interval["start"]) > cursor:
                    break
                cursor = max(cursor, int(interval["end"]))
            stores[family] = {
                "intervals": intervals,
                "interval_count": len(intervals),
                "epoch_count": len({item["epoch_id"] for item in intervals}),
                "truncated": truncated,
                "covered_start": int(intervals[0]["start"]) if intervals else 0,
                "covered_end": int(intervals[-1]["end"]) if intervals else 0,
                "complete": bool(intervals and cursor >= requested_end),
            }
        # "server" and "rollups" are legacy wire aliases of the raw store:
        # every read serves from the graduated buckets (the persisted rollup
        # table is retired), so the aliases share the raw sampler epochs
        # without duplicating interval rows in SQLite.
        stores["server"] = copy.deepcopy(stores["raw"])
        stores["rollups"] = copy.deepcopy(stores["raw"])
        raw = stores["raw"]
        epoch_summaries: dict[str, dict[str, Any]] = {}
        for family, facts in stores.items():
            for interval in facts["intervals"]:
                epoch_id = str(interval["epoch_id"])
                summary = epoch_summaries.setdefault(epoch_id, {
                    "epoch_id": epoch_id,
                    "start": int(interval["start"]),
                    "end": int(interval["end"]),
                    "families": [],
                    "owner_generation": int(interval.get("owner_generation") or 0),
                    "source": str(interval.get("source") or ""),
                })
                summary["start"] = min(int(summary["start"]), int(interval["start"]))
                summary["end"] = max(int(summary["end"]), int(interval["end"]))
                if family not in summary["families"]:
                    summary["families"].append(family)
        epochs = sorted(epoch_summaries.values(), key=lambda item: (int(item["start"]), str(item["epoch_id"])))
        epochs_truncated = len(epochs) > STATS_COVERAGE_MAX_INTERVALS
        if epochs_truncated:
            epochs = epochs[-STATS_COVERAGE_MAX_INTERVALS:]
        available_start = int(row[0] or 0)
        available_end = int(row[1] or 0)
        return {
            "available_start": available_start,
            "available_end": available_end,
            "retained_resolution": int(row[2] or 0),
            "source_records": int(row[3] or 0),
            "intervals": raw["intervals"],
            "store_intervals": {family: facts["intervals"] for family, facts in stores.items()},
            "stores": stores,
            "epochs": epochs,
            "epochs_truncated": epochs_truncated,
        }

    def coverage_integrity_report(self) -> dict[str, Any]:
        """Read-only durable coverage self-check for boot/on-demand monitoring.

        Returns the count of cross-epoch overlapping pairs and inverted rows per
        family so the system can detect a broken coverage table itself instead
        of waiting for a blank chart.  ``ok`` is True when the disjointness
        invariant holds.
        """

        connection = self._connection()
        overlaps = self._coverage_overlap_pairs(connection)
        inverted = int(connection.execute(
            "SELECT COUNT(*) FROM stats_coverage_intervals WHERE end <= start"
        ).fetchone()[0])
        offenders = [
            {"family": str(row[0]), "overlaps": int(row[1])}
            for row in connection.execute(
                "SELECT a.family, COUNT(*) FROM stats_coverage_intervals a "
                "JOIN stats_coverage_intervals b ON a.family=b.family AND a.rowid<b.rowid "
                "AND a.end > b.start AND b.end > a.start GROUP BY a.family"
            ).fetchall()
        ]
        return {
            "ok": overlaps == 0 and inverted == 0,
            "overlapping_pairs": overlaps,
            "inverted_rows": inverted,
            "families": offenders,
        }

    def retain_after(self, cutoff_time: float) -> int:
        connection = self._connection()
        with connection:
            cursor = connection.execute("DELETE FROM stats_buckets WHERE start + duration < ?", (float(cutoff_time),))
            connection.execute("DELETE FROM stats_coverage_intervals WHERE end < ?", (float(cutoff_time),))
        return int(cursor.rowcount)

    def diagnostics(self) -> dict[str, Any]:
        connection = self._connection()
        rows = int(connection.execute("SELECT COUNT(*) FROM stats_buckets").fetchone()[0])
        sequence = int(connection.execute("SELECT COALESCE(MAX(sequence), 0) FROM stats_buckets").fetchone()[0])
        wal_path = self.path.with_name(f"{self.path.name}-wal")
        return {
            "schema_version": STATS_STORE_SCHEMA_VERSION,
            "rows": rows,
            "sequence": sequence,
            "database_bytes": self.path.stat().st_size if self.path.exists() else 0,
            "wal_bytes": wal_path.stat().st_size if wal_path.exists() else 0,
        }
