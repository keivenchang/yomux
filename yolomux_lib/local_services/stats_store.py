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
from pathlib import Path
from typing import Any


STATS_STORE_SCHEMA_VERSION = 2
STATS_STORE_MAX_JSON_BYTES = 256 * 1024
STATS_STORE_MAX_ROWS_PER_QUERY = 20_000
STATS_COST_SUMMARY_MAX_COMPONENTS = 4096
STATS_COST_SUMMARY_MAX_BYTES = 160 * 1024

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
SERVER_FIELDS = (
    "cpu_total_percent",
    "cpu_count",
    "system_cpu_total_percent",
    "system_cpu_count",
    "ask_agent_total",
    "run_agent_total",
    "transition_agent_total",
    "idle_agent_total",
    "active_agent_total",
    "inactive_agent_total",
    "agent_activity_samples",
    "tokens_per_agent_total",
    "agent_token_samples",
)


def empty_host_metrics() -> dict[str, Any]:
    return {
        "cpu_label": "",
        "system_memory_label": "",
        "system_memory_used_total_bytes": 0.0,
        "system_memory_capacity_total_bytes": 0.0,
        "system_memory_count": 0.0,
        "cpu_processes": {},
        "memory_processes": {},
        "gpu_util_processes": {},
        "gpu_memory_processes": {},
        "gpu_devices": {},
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


def normalize_bucket(value: Any) -> dict[str, Any]:
    """Return one bounded, JSON-safe bucket without trusting persisted input."""
    raw = value if isinstance(value, dict) else {}
    result = empty_bucket(int(_finite(raw.get("start"))), max(1, int(_finite(raw.get("duration")))))
    result["sequence"] = int(_finite(raw.get("sequence")))
    result["server_sequence"] = int(_finite(raw.get("server_sequence")))
    for field in SERVER_FIELDS:
        result[field] = _finite(raw.get(field))
    for mapping_field in ("agent_token_rates", "cost_summary", "host_metrics", "clients", "servers"):
        candidate = raw.get(mapping_field)
        if isinstance(candidate, dict):
            result[mapping_field] = copy.deepcopy(candidate)
    return result


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

    Bucket rows retain the legacy response shape in JSON for compatibility, and
    normalized child tables give bounded relational identities for clients,
    processes, agent rates, and host metrics.  All rows for one bucket revision
    are committed or rolled back together.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.connection: sqlite3.Connection | None = None

    def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
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
            CREATE TABLE IF NOT EXISTS stats_clients (
              start INTEGER NOT NULL, duration INTEGER NOT NULL, client_id TEXT NOT NULL,
              sequence INTEGER NOT NULL, values_json TEXT NOT NULL,
              PRIMARY KEY (start, duration, client_id),
              FOREIGN KEY (start, duration) REFERENCES stats_buckets(start, duration) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS stats_processes (
              start INTEGER NOT NULL, duration INTEGER NOT NULL, process_id TEXT NOT NULL,
              sequence INTEGER NOT NULL, values_json TEXT NOT NULL,
              PRIMARY KEY (start, duration, process_id),
              FOREIGN KEY (start, duration) REFERENCES stats_buckets(start, duration) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS stats_agent_rates (
              start INTEGER NOT NULL, duration INTEGER NOT NULL, rate_key TEXT NOT NULL,
              values_json TEXT NOT NULL,
              PRIMARY KEY (start, duration, rate_key),
              FOREIGN KEY (start, duration) REFERENCES stats_buckets(start, duration) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS stats_host_metrics (
              start INTEGER NOT NULL, duration INTEGER NOT NULL, metric_key TEXT NOT NULL,
              values_json TEXT NOT NULL,
              PRIMARY KEY (start, duration, metric_key),
              FOREIGN KEY (start, duration) REFERENCES stats_buckets(start, duration) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS stats_rollups (
              start INTEGER NOT NULL, duration INTEGER NOT NULL, sequence INTEGER NOT NULL,
              bucket_json TEXT NOT NULL,
              PRIMARY KEY (start, duration)
            );
            CREATE INDEX IF NOT EXISTS stats_rollups_duration_start ON stats_rollups(duration, start);
            """
        )
        connection.execute(
            "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(STATS_STORE_SCHEMA_VERSION),),
        )
        connection.commit()
        self.connection = connection

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
        normalized = normalize_bucket(bucket)
        start, duration = normalized["start"], normalized["duration"]
        encoded_bucket = self._encode(normalized)
        clients = normalized.get("clients") if isinstance(normalized.get("clients"), dict) else {}
        processes = normalized.get("servers") if isinstance(normalized.get("servers"), dict) else {}
        rates = normalized.get("agent_token_rates") if isinstance(normalized.get("agent_token_rates"), dict) else {}
        host_metrics = normalized.get("host_metrics") if isinstance(normalized.get("host_metrics"), dict) else {}
        connection.execute(
            "INSERT INTO stats_buckets(start,duration,sequence,server_sequence,bucket_json) VALUES(?,?,?,?,?) "
            "ON CONFLICT(start,duration) DO UPDATE SET sequence=excluded.sequence,server_sequence=excluded.server_sequence,bucket_json=excluded.bucket_json",
            (start, duration, normalized["sequence"], normalized["server_sequence"], encoded_bucket),
        )
        for table in ("stats_clients", "stats_processes", "stats_agent_rates", "stats_host_metrics"):
            connection.execute(f"DELETE FROM {table} WHERE start=? AND duration=?", (start, duration))
        for client_id, values in clients.items():
            if isinstance(values, dict):
                connection.execute("INSERT INTO stats_clients VALUES(?,?,?,?,?)", (start, duration, str(client_id), int(_finite(values.get("sequence"))), self._encode(values)))
        for process_id, values in processes.items():
            if isinstance(values, dict):
                connection.execute("INSERT INTO stats_processes VALUES(?,?,?,?,?)", (start, duration, str(process_id), int(_finite(values.get("sequence"))), self._encode(values)))
        for rate_key, values in rates.items():
            if isinstance(values, dict):
                connection.execute("INSERT INTO stats_agent_rates VALUES(?,?,?,?)", (start, duration, str(rate_key), self._encode(values)))
        for metric_key, values in host_metrics.items():
            if isinstance(values, dict):
                connection.execute("INSERT INTO stats_host_metrics VALUES(?,?,?,?)", (start, duration, str(metric_key), self._encode(values)))

    def upsert_bucket(self, bucket: dict[str, Any]) -> None:
        connection = self._connection()
        with connection:
            self._upsert_bucket(connection, bucket)

    def bucket(self, start: int, duration: int) -> dict[str, Any] | None:
        row = self._connection().execute(
            "SELECT bucket_json FROM stats_buckets WHERE start=? AND duration=?",
            (int(start), int(duration)),
        ).fetchone()
        return normalize_bucket(json.loads(str(row[0]))) if row is not None else None

    def rollup_bucket(self, start: int, duration: int) -> dict[str, Any] | None:
        row = self._connection().execute(
            "SELECT bucket_json FROM stats_rollups WHERE start=? AND duration=?",
            (int(start), int(duration)),
        ).fetchone()
        return normalize_bucket(json.loads(str(row[0]))) if row is not None else None

    def upsert_rollup(self, bucket: dict[str, Any]) -> None:
        normalized = normalize_bucket(bucket)
        connection = self._connection()
        with connection:
            connection.execute(
                "INSERT INTO stats_rollups(start,duration,sequence,bucket_json) VALUES(?,?,?,?) "
                "ON CONFLICT(start,duration) DO UPDATE SET sequence=excluded.sequence,bucket_json=excluded.bucket_json",
                (normalized["start"], normalized["duration"], normalized["sequence"], self._encode(normalized)),
            )

    def query_rollups(self, *, duration: int, start: int = 0, end: int = 0) -> list[dict[str, Any]]:
        clauses = ["duration = ?"]
        values: list[Any] = [max(1, int(duration))]
        if start:
            clauses.append("start + duration > ?")
            values.append(max(0, int(start)))
        if end:
            clauses.append("start < ?")
            values.append(max(0, int(end)))
        rows = self._connection().execute(
            f"SELECT bucket_json FROM stats_rollups WHERE {' AND '.join(clauses)} ORDER BY start",
            values,
        ).fetchall()
        return [normalize_bucket(json.loads(str(row[0]))) for row in rows]

    def replace_buckets(self, buckets: list[dict[str, Any]]) -> None:
        """Atomically replace the durable history after compaction/import."""
        connection = self._connection()
        normalized = [normalize_bucket(bucket) for bucket in buckets]
        with connection:
            for table in ("stats_clients", "stats_processes", "stats_agent_rates", "stats_host_metrics", "stats_buckets"):
                connection.execute(f"DELETE FROM {table}")
            for bucket in normalized:
                self._upsert_bucket(connection, bucket)

    def replace_buckets_with_marker(self, buckets: list[dict[str, Any]], marker_key: str, marker_value: str) -> None:
        """Atomically replace durable history and record the completed migration."""
        connection = self._connection()
        normalized = [normalize_bucket(bucket) for bucket in buckets]
        with connection:
            for table in ("stats_clients", "stats_processes", "stats_agent_rates", "stats_host_metrics", "stats_buckets"):
                connection.execute(f"DELETE FROM {table}")
            for bucket in normalized:
                self._upsert_bucket(connection, bucket)
            connection.execute(
                "INSERT INTO schema_meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(marker_key), str(marker_value)),
            )

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
        return [normalize_bucket(json.loads(str(row[0]))) for row in rows]

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
        return [normalize_bucket(json.loads(str(row[0]))) for row in rows]

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
        return [normalize_bucket(json.loads(str(row[0]))) for row in rows]

    def latest_sequence(self) -> int:
        row = self._connection().execute("SELECT COALESCE(MAX(sequence), 0) FROM stats_buckets").fetchone()
        return int(row[0] or 0)

    def query_coverage(self, *, start: int = 0, end: int = 0) -> dict[str, int]:
        """Return bounded range facts without materializing historical buckets."""
        clauses: list[str] = []
        values: list[int] = []
        if start:
            clauses.append("start + duration > ?")
            values.append(max(0, int(start)))
        if end:
            clauses.append("start < ?")
            values.append(max(0, int(end)))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        row = self._connection().execute(
            f"SELECT COALESCE(MIN(start), 0), COALESCE(MAX(start + duration), 0), COALESCE(MAX(duration), 0), COUNT(*) FROM stats_buckets {where}",
            values,
        ).fetchone()
        return {
            "available_start": int(row[0] or 0),
            "available_end": int(row[1] or 0),
            "retained_resolution": int(row[2] or 0),
            "source_records": int(row[3] or 0),
        }

    def retain_after(self, cutoff_time: float) -> int:
        connection = self._connection()
        with connection:
            cursor = connection.execute("DELETE FROM stats_buckets WHERE start + duration < ?", (float(cutoff_time),))
        return int(cursor.rowcount)

    def diagnostics(self) -> dict[str, Any]:
        connection = self._connection()
        rows = int(connection.execute("SELECT COUNT(*) FROM stats_buckets").fetchone()[0])
        sequence = int(connection.execute("SELECT COALESCE(MAX(sequence), 0) FROM stats_buckets").fetchone()[0])
        children = {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("stats_clients", "stats_processes", "stats_agent_rates", "stats_host_metrics")
        }
        wal_path = self.path.with_name(f"{self.path.name}-wal")
        return {
            "schema_version": STATS_STORE_SCHEMA_VERSION,
            "rows": rows,
            "sequence": sequence,
            "children": children,
            "database_bytes": self.path.stat().st_size if self.path.exists() else 0,
            "wal_bytes": wal_path.stat().st_size if wal_path.exists() else 0,
        }
