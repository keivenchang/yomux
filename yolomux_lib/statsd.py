"""Persistent single-writer YO!stats service.

This is introduced alongside the legacy in-process history owner.  It stores
the exact bucket shape and validates the service lifecycle before P6 redirects
the public endpoint to it, avoiding two normal writers during a rolling deploy.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import os
import sqlite3
import threading
import time
from pathlib import Path
from time import monotonic as monotonic_clock
from typing import Any

from .control import send_yolomux_control_request
from .common import STATE_DIR
from .local_services.rpc import LOCAL_RPC_VERSION
from .local_services.rpc import safe_socket_path
from .local_services.stats_store import StatsStore
from .local_services import stats_store
from .local_services.client import LocalServiceClient
from .local_services.runtime import acquire_client_lease
from .local_services.runtime import apply_service_process_priority
from .local_services.runtime import redact_local_service_text
from .local_services.runtime import release_client_lease
from .local_services.runtime import run_local_rpc_service
from . import session_files


# The transport envelope remains at ``LOCAL_RPC_VERSION``. This version names
# the statsd RPC contract and forces old children to restart when new actions
# or response fields are added during a rolling server update.
STATSD_PROTOCOL_VERSION = 4
STATSD_DEFAULT_IDLE_SECONDS = 300.0
STATSD_SOCKET_NAME = "statsd.sock"
STATSD_DATABASE_NAME = "stats-history.sqlite3"
STATSD_LEGACY_IMPORT_VERSION = 1
STATSD_LEGACY_IMPORT_MARKER = "legacy_import_version"
STATSD_AGENT_TOKEN_STATE_KEY = "agent_token_state"
STATSD_AGENT_TOKEN_RECOVERY_MARKER = "agent_token_history_recovery_version"
STATSD_AGENT_TOKEN_RECOVERY_VERSION = 1
STATS_HISTORY_RETENTION_SECONDS = 24 * 60 * 60
STATS_HISTORY_RAW_WINDOW_SECONDS = 30 * 60
STATS_HISTORY_MIDDLE_WINDOW_SECONDS = 2 * 60 * 60
STATS_HISTORY_ROLLUP_BUCKET_SECONDS = 60
STATS_HISTORY_TIERS = (
    (STATS_HISTORY_RAW_WINDOW_SECONDS, 1),
    (STATS_HISTORY_MIDDLE_WINDOW_SECONDS, 10),
    (4 * 60 * 60, STATS_HISTORY_ROLLUP_BUCKET_SECONDS),
    (8 * 60 * 60, 2 * 60),
    (12 * 60 * 60, 5 * 60),
    (STATS_HISTORY_RETENTION_SECONDS, 10 * 60),
)
STATS_AGENT_TOKEN_BUCKET_SECONDS = 60
STATS_AGENT_TOKEN_MAX_ATTRIBUTION_GAP_SECONDS = 180.0
STATS_AGENT_TOKEN_SCHEMA_VERSION = 3
STATS_AGENT_TOKEN_HISTORY_FIELDS = ("tokens_per_agent_total", "agent_token_samples")
STATS_HISTORY_CLIENT_ID_MAX_LENGTH = 96
STATS_HISTORY_POST_MAX_RECORDS = 1000
STATSD_SAMPLE_INTERVAL_SECONDS = 1.0


def stats_history_client_id(value: Any = "") -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return "".join(character if character.isalnum() or character in "_.:-" else "-" for character in raw)[:STATS_HISTORY_CLIENT_ID_MAX_LENGTH]


def default_socket_path() -> Path:
    return safe_socket_path(STATE_DIR / "services" / STATSD_SOCKET_NAME, prefix="yolomux-statsd")


def default_database_path() -> Path:
    return STATE_DIR / STATSD_DATABASE_NAME


def default_legacy_client_history_paths(state_dir: Path = STATE_DIR) -> list[Path]:
    return [
        state_dir / "stats-client-history-v4.json",
        state_dir / "stats-client-history-v3.json",
        state_dir / "stats-client-history.json",
    ]


def default_legacy_shared_history_path(state_dir: Path = STATE_DIR) -> Path:
    return state_dir / "tmux-AI-status.json"


class PersistentStatsService:
    """The only process allowed to mutate the row-based stats database."""

    def __init__(self, socket_path: Path, database_path: Path, idle_seconds: float = STATSD_DEFAULT_IDLE_SECONDS):
        self.socket_path = safe_socket_path(socket_path, prefix="yolomux-statsd")
        self.lock_path = self.socket_path.with_suffix(".lock")
        self.store = StatsStore(database_path)
        self.stop_event = threading.Event()
        self.leases: dict[str, int] = {}
        self.idle_seconds = max(1.0, float(idle_seconds))
        self.started_at = time.time()
        self.last_client_at = time.monotonic()
        self._encoded_query_cache: dict[tuple[Any, ...], tuple[dict[str, Any], float]] = {}
        self._query_cache_ttl_seconds = 1.0
        self.sampler_owner: dict[str, Any] = {}
        self.agent_token_consumer_until = 0.0
        self.last_sampler_success_at = 0.0
        self.last_sampler_failure = ""
        self.last_sampler_attempt_at = 0.0
        self.sampler_thread: threading.Thread | None = None
        self.sampler_wake_event = threading.Event()

    def _sampler_loop(self) -> None:
        while not self.stop_event.is_set():
            owner = dict(self.sampler_owner)
            if owner:
                self.last_sampler_attempt_at = time.time()
                response = send_yolomux_control_request(
                    owner,
                    {"action": "statsd_sample", "token_consumer": time.time() < self.agent_token_consumer_until},
                    timeout=0.9,
                )
                if not response.get("ok"):
                    self.last_sampler_failure = redact_local_service_text(response.get("error") or "stats owner unavailable")
                else:
                    self.last_sampler_success_at = self.last_sampler_attempt_at
                    self.last_sampler_failure = ""
            self.sampler_wake_event.wait(STATSD_SAMPLE_INTERVAL_SECONDS)
            self.sampler_wake_event.clear()

    @staticmethod
    def _legacy_client_bucket(snapshot: Any) -> dict[str, Any] | None:
        if not isinstance(snapshot, list) or len(snapshot) not in (4, 5):
            return None
        start, duration, sequence, clients = snapshot[:4]
        servers = snapshot[4] if len(snapshot) == 5 else {}
        try:
            bucket = stats_store.empty_bucket(int(start), int(duration))
            bucket["sequence"] = max(0, int(sequence))
        except (TypeError, ValueError):
            return None
        if bucket["start"] <= 0:
            return None
        if isinstance(clients, dict):
            bucket["clients"] = copy.deepcopy(clients)
        if isinstance(servers, dict):
            bucket["servers"] = copy.deepcopy(servers)
        return bucket

    @staticmethod
    def _legacy_shared_bucket(snapshot: Any) -> dict[str, Any] | None:
        if isinstance(snapshot, dict):
            source = snapshot
        elif isinstance(snapshot, list) and len(snapshot) >= 4 + len(stats_store.SERVER_FIELDS) + 1:
            start, duration, sequence, server_sequence, *values = snapshot
            source = {
                "start": start,
                "duration": duration,
                "sequence": sequence,
                "server_sequence": server_sequence,
                **dict(zip(stats_store.SERVER_FIELDS, values[:len(stats_store.SERVER_FIELDS)], strict=True)),
                "agent_token_rates": values[len(stats_store.SERVER_FIELDS)],
                "host_metrics": values[len(stats_store.SERVER_FIELDS) + 1] if len(values) > len(stats_store.SERVER_FIELDS) + 1 else stats_store.empty_host_metrics(),
            }
        else:
            return None
        try:
            bucket = stats_store.normalize_bucket(source)
        except (TypeError, ValueError):
            return None
        if bucket["start"] <= 0:
            return None
        return bucket

    @staticmethod
    def _normalize_legacy_agent_token_intervals(bucket: dict[str, Any]) -> None:
        duration = max(0.0, float(bucket.get("duration") or 0.0))
        rates = bucket.get("agent_token_rates") if isinstance(bucket.get("agent_token_rates"), dict) else {}
        changed = False
        token_total = 0.0
        sample_total = 0.0
        for item in rates.values():
            if not isinstance(item, dict):
                continue
            try:
                tokens = max(0.0, float(item.get("tokens", item.get("total", 0.0)) or 0.0))
                seconds = max(0.0, float(item.get("seconds") or 0.0))
            except (TypeError, ValueError):
                continue
            if duration > 0 and seconds > duration:
                tokens *= duration / seconds
                seconds = duration
                changed = True
            item["tokens"] = tokens
            item["total"] = tokens
            item["seconds"] = seconds
            item["samples"] = 1.0 if tokens > 0 or seconds > 0 else 0.0
            token_total += tokens
            sample_total += float(item["samples"])
        if changed:
            bucket["tokens_per_agent_total"] = token_total
            bucket["agent_token_samples"] = sample_total

    @staticmethod
    def _bucket_seconds(sample_time: float, now: float) -> int:
        age = max(0.0, now - sample_time)
        for max_age_seconds, bucket_seconds in STATS_HISTORY_TIERS:
            if age <= max_age_seconds:
                return bucket_seconds
        return STATS_HISTORY_TIERS[-1][1]

    @staticmethod
    def _positive_finite(value: Any) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0.0
        return number if math.isfinite(number) and number > 0.0 else 0.0

    def merge_records(self, records: list[dict[str, Any]], *, client_id: str = "", now: float | None = None, clear: bool = False) -> dict[str, Any]:
        """Apply bounded browser deltas through the durable single writer."""
        if len(records) > STATS_HISTORY_POST_MAX_RECORDS:
            raise ValueError(f"records limit is {STATS_HISTORY_POST_MAX_RECORDS}")
        sample_now = float(time.time() if now is None else now)
        if not math.isfinite(sample_now):
            raise ValueError("now must be finite")
        if clear:
            self.store.replace_buckets([])
        clean_client_id = stats_history_client_id(client_id)
        next_sequence = int(self.store.diagnostics().get("sequence") or 0)
        changed = 0
        for record in records:
            if not isinstance(record, dict):
                continue
            try:
                sample_time = float(record.get("start", record.get("time", sample_now)))
            except (TypeError, ValueError):
                sample_time = sample_now
            if not math.isfinite(sample_time) or sample_time < sample_now - STATS_HISTORY_RETENTION_SECONDS:
                continue
            duration = self._bucket_seconds(sample_time, sample_now)
            start = int(math.floor(sample_time / duration) * duration)
            bucket = self.store.bucket(start, duration) or stats_store.empty_bucket(start, duration)
            clients = bucket.setdefault("clients", {})
            client = clients.get(clean_client_id)
            if not isinstance(client, dict):
                client = stats_store.empty_client_bucket()
                clients[clean_client_id] = client
            record_changed = False
            for field in stats_store.BROWSER_FIELDS:
                value = self._positive_finite(record.get(field))
                if value:
                    client[field] = float(client.get(field) or 0.0) + value
                    record_changed = True
            if not record_changed:
                continue
            next_sequence += 1
            client["sequence"] = next_sequence
            bucket["sequence"] = max(int(bucket.get("sequence") or 0), next_sequence)
            self.store.upsert_bucket(bucket)
            changed += 1
        if changed:
            self._compact_history(sample_now)
            self._encoded_query_cache.clear()
        return {"ok": True, "changed": changed, "sequence": int(self.store.diagnostics().get("sequence") or 0)}

    @staticmethod
    def _merge_host_metrics(bucket: dict[str, Any], metrics: Any) -> bool:
        if not isinstance(metrics, dict):
            return False
        target = bucket.setdefault("host_metrics", stats_store.empty_host_metrics())
        changed = False
        for field in ("cpu_label", "system_memory_label"):
            label = str(metrics.get(field) or "").strip()
            if label:
                target[field] = label
                changed = True
        try:
            memory_used = max(0.0, float(metrics.get("system_memory_used_bytes") or 0.0))
            memory_capacity = max(0.0, float(metrics.get("system_memory_capacity_bytes") or 0.0))
        except (TypeError, ValueError):
            memory_used = 0.0
            memory_capacity = 0.0
        if memory_used or memory_capacity:
            target["system_memory_used_total_bytes"] = float(target.get("system_memory_used_total_bytes") or 0.0) + memory_used
            target["system_memory_capacity_total_bytes"] = float(target.get("system_memory_capacity_total_bytes") or 0.0) + memory_capacity
            target["system_memory_count"] = float(target.get("system_memory_count") or 0.0) + 1.0
            changed = True
        for field in ("cpu_processes", "memory_processes", "gpu_util_processes", "gpu_memory_processes"):
            source = metrics.get(field)
            if not isinstance(source, dict):
                continue
            values = target.setdefault(field, {})
            value_key = "total_bytes" if field in ("memory_processes", "gpu_memory_processes") else "total_percent"
            for raw_key, raw_item in source.items():
                if not isinstance(raw_item, dict):
                    continue
                key = str(raw_key or "").strip()
                value = PersistentStatsService._positive_finite(raw_item.get("value"))
                if not key:
                    continue
                item = values.setdefault(key, {"label": str(raw_item.get("label") or key), value_key: 0.0, "samples": 0.0})
                item[value_key] = float(item.get(value_key) or 0.0) + value
                item["samples"] = float(item.get("samples") or 0.0) + 1.0
                item["label"] = str(raw_item.get("label") or item.get("label") or key)
                changed = True
        devices = metrics.get("gpu_devices")
        if isinstance(devices, dict):
            values = target.setdefault("gpu_devices", {})
            for raw_key, raw_item in devices.items():
                if not isinstance(raw_item, dict):
                    continue
                key = str(raw_key or "").strip()
                if not key:
                    continue
                item = values.setdefault(key, {"label": str(raw_item.get("label") or key), "util_total_percent": 0.0, "memory_used_total_bytes": 0.0, "memory_capacity_total_bytes": 0.0, "samples": 0.0})
                item["label"] = str(raw_item.get("label") or item.get("label") or key)
                item["util_total_percent"] = float(item.get("util_total_percent") or 0.0) + min(100.0, PersistentStatsService._positive_finite(raw_item.get("util_percent")))
                item["memory_used_total_bytes"] = float(item.get("memory_used_total_bytes") or 0.0) + PersistentStatsService._positive_finite(raw_item.get("memory_used_bytes"))
                item["memory_capacity_total_bytes"] = float(item.get("memory_capacity_total_bytes") or 0.0) + PersistentStatsService._positive_finite(raw_item.get("memory_capacity_bytes"))
                item["samples"] = float(item.get("samples") or 0.0) + 1.0
                changed = True
        return changed

    def merge_server_records(self, records: list[dict[str, Any]], *, now: float | None = None) -> dict[str, Any]:
        """Apply owner-only server, process, token, and host deltas durably."""
        if len(records) > STATS_HISTORY_POST_MAX_RECORDS:
            raise ValueError(f"records limit is {STATS_HISTORY_POST_MAX_RECORDS}")
        sample_now = float(time.time() if now is None else now)
        if not math.isfinite(sample_now):
            raise ValueError("now must be finite")
        next_sequence = int(self.store.diagnostics().get("sequence") or 0)
        changed = 0
        for record in records:
            if not isinstance(record, dict):
                continue
            try:
                sample_time = float(record.get("start", record.get("time", sample_now)))
            except (TypeError, ValueError):
                sample_time = sample_now
            if not math.isfinite(sample_time) or sample_time < sample_now - STATS_HISTORY_RETENTION_SECONDS:
                continue
            duration = self._bucket_seconds(sample_time, sample_now)
            start = int(math.floor(sample_time / duration) * duration)
            bucket = self.store.bucket(start, duration) or stats_store.empty_bucket(start, duration)
            record_changed = False
            for field in stats_store.SERVER_FIELDS:
                value = self._positive_finite(record.get(field))
                if value:
                    bucket[field] = float(bucket.get(field) or 0.0) + value
                    record_changed = True
            rates = record.get("agent_token_rates")
            if isinstance(rates, list):
                target_rates = bucket.setdefault("agent_token_rates", {})
                for raw_rate in rates:
                    if not isinstance(raw_rate, dict):
                        continue
                    key = str(raw_rate.get("key") or "").strip()
                    if not key:
                        continue
                    target = target_rates.setdefault(key, {"label": str(raw_rate.get("label") or key), "total": 0.0, "samples": 0.0, "tokens": 0.0, "seconds": 0.0, "source": ""})
                    for field in ("total", "samples", "tokens", "seconds"):
                        target[field] = float(target.get(field) or 0.0) + self._positive_finite(raw_rate.get(field))
                    target["label"] = str(raw_rate.get("label") or target.get("label") or key)
                    if raw_rate.get("source"):
                        target["source"] = str(raw_rate["source"])
                    record_changed = True
            record_changed = self._merge_host_metrics(bucket, record.get("host_metrics")) or record_changed
            process = record.get("process")
            if isinstance(process, dict):
                process_id = str(process.get("id") or "").strip()
                if process_id:
                    processes = bucket.setdefault("servers", {})
                    target = processes.setdefault(process_id, stats_store.empty_process_bucket())
                    target["label"] = str(process.get("label") or target.get("label") or process_id)
                    for field in ("pid", "port"):
                        try:
                            target[field] = max(0, int(process.get(field) or target.get(field) or 0))
                        except (TypeError, ValueError):
                            pass
                    try:
                        target["started_at"] = max(0.0, float(process.get("started_at") or target.get("started_at") or 0.0))
                    except (TypeError, ValueError):
                        pass
                    for field, source_field in (("cpu_total_percent", "cpu_percent"), ("cpu_count", "cpu_count")):
                        value = self._positive_finite(process.get(source_field))
                        if value:
                            target[field] = float(target.get(field) or 0.0) + value
                            record_changed = True
                    if record_changed:
                        next_sequence += 1
                        target["sequence"] = next_sequence
            if not record_changed:
                continue
            next_sequence += 1
            bucket["server_sequence"] = next_sequence
            bucket["sequence"] = max(int(bucket.get("sequence") or 0), next_sequence)
            self.store.upsert_bucket(bucket)
            changed += 1
        if changed:
            self._compact_history(sample_now)
            self._encoded_query_cache.clear()
        return {"ok": True, "changed": changed, "sequence": int(self.store.diagnostics().get("sequence") or 0)}

    @staticmethod
    def _agent_token_state_snapshot(value: Any) -> dict[str, dict[str, Any]]:
        snapshot: dict[str, dict[str, Any]] = {}
        if not isinstance(value, dict):
            return snapshot
        for raw_key, raw_item in value.items():
            key = str(raw_key or "").strip()
            if not key or not isinstance(raw_item, dict):
                continue
            item: dict[str, Any] = {}
            for field in ("tokens", "time"):
                try:
                    item[field] = max(0.0, float(raw_item.get(field) or 0.0))
                except (TypeError, ValueError):
                    item[field] = 0.0
            for field in ("label", "source", "identity"):
                text = str(raw_item.get(field) or "").strip()
                if text:
                    item[field] = text
            snapshot[key] = item
        return snapshot

    def _agent_token_state(self, fallback: Any = None) -> dict[str, dict[str, Any]]:
        raw = self.store.metadata_value(STATSD_AGENT_TOKEN_STATE_KEY)
        if raw:
            try:
                decoded = json.loads(raw)
            except json.JSONDecodeError:
                decoded = {}
            state = self._agent_token_state_snapshot(decoded)
            if state:
                return state
        return self._agent_token_state_snapshot(fallback)

    def _set_agent_token_state(self, state: dict[str, dict[str, Any]]) -> None:
        self.store.set_metadata_value(STATSD_AGENT_TOKEN_STATE_KEY, json.dumps(state, sort_keys=True, separators=(",", ":")))

    @staticmethod
    def _agent_token_delta_records(key: str, label: str, start_time: float, end_time: float, token_delta: float) -> list[dict[str, Any]]:
        if not key or not math.isfinite(start_time) or not math.isfinite(end_time) or end_time <= start_time:
            return []
        if not math.isfinite(token_delta) or token_delta <= 0:
            return []
        elapsed = end_time - start_time
        records: list[dict[str, Any]] = []
        cursor = start_time
        while cursor < end_time:
            bucket_start = math.floor(cursor / STATS_AGENT_TOKEN_BUCKET_SECONDS) * STATS_AGENT_TOKEN_BUCKET_SECONDS
            bucket_end = min(end_time, bucket_start + STATS_AGENT_TOKEN_BUCKET_SECONDS)
            overlap = max(0.0, bucket_end - cursor)
            if overlap <= 0:
                cursor = min(end_time, cursor + STATS_AGENT_TOKEN_BUCKET_SECONDS)
                continue
            tokens = token_delta * (overlap / elapsed)
            records.append({
                "time": bucket_start,
                "tokens_per_agent_total": tokens,
                "agent_token_samples": 1.0,
                "agent_token_rates": [{
                    "key": key,
                    "label": label,
                    "total": tokens,
                    "samples": 1.0,
                    "tokens": tokens,
                    "seconds": overlap,
                    "source": "transcript",
                }],
            })
            cursor = bucket_end
        return records

    def claim_agent_token_deltas(
        self,
        token_measurements: list[dict[str, Any]],
        seen_keys: set[str],
        sample_time: float,
        fallback_state: Any = None,
    ) -> dict[str, Any]:
        """Atomically claim transcript counter advances in statsd-owned metadata."""

        if not math.isfinite(sample_time):
            raise ValueError("sample_time must be finite")
        state = self._agent_token_state(fallback_state)
        records: list[dict[str, Any]] = []
        for measurement in token_measurements:
            if not isinstance(measurement, dict):
                continue
            key = str(measurement.get("key") or "").strip()
            if not key:
                continue
            try:
                token_count = max(0.0, float(measurement.get("tokens") or 0.0))
            except (TypeError, ValueError):
                continue
            token_source = str(measurement.get("source") or "").strip()
            token_identity = str(measurement.get("identity") or token_source).strip() or token_source
            label = str(measurement.get("label") or key)
            previous = state.get(key)
            previous_source = str(previous.get("source") or "") if isinstance(previous, dict) else ""
            previous_identity = str(previous.get("identity") or previous_source) if isinstance(previous, dict) else ""
            previous_tokens = float(previous.get("tokens") or 0.0) if isinstance(previous, dict) else 0.0
            previous_time = float(previous.get("time") or 0.0) if isinstance(previous, dict) else 0.0
            elapsed = sample_time - previous_time
            if (
                previous
                and previous_source == token_source
                and previous_identity == token_identity
                and token_count >= previous_tokens
                and 0 < elapsed <= STATS_AGENT_TOKEN_MAX_ATTRIBUTION_GAP_SECONDS
            ):
                records.extend(self._agent_token_delta_records(key, label, previous_time, sample_time, token_count - previous_tokens))
            state[key] = {"tokens": token_count, "time": sample_time, "label": label, "source": token_source, "identity": token_identity}
        for key in list(state):
            if key not in seen_keys:
                state.pop(key, None)
        self._set_agent_token_state(state)
        return {"ok": True, "records": records, "state": state}

    def claim_agent_token_deltas_from_rows(
        self,
        rows: list[dict[str, Any]],
        seen_keys: set[str],
        sample_time: float,
        fallback_state: Any = None,
    ) -> dict[str, Any]:
        measurements: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            transcript = str(row.get("transcript") or "").strip()
            kind = str(row.get("kind") or "").strip().lower()
            if not transcript:
                continue
            transcript_path = Path(transcript)
            generated_tokens = session_files.transcript_generated_tokens(transcript_path, kind)
            if generated_tokens is None:
                continue
            key = str(row.get("key") or "").strip()
            if not key:
                continue
            measurements.append({
                "key": key,
                "label": str(row.get("label") or key),
                "tokens": generated_tokens,
                "source": "transcript",
                "identity": session_files.transcript_usage_identity(transcript_path, kind),
            })
        return self.claim_agent_token_deltas(measurements, seen_keys, sample_time, fallback_state=fallback_state)

    @staticmethod
    def _agent_token_rate_records(rates: Any) -> list[dict[str, Any]]:
        if isinstance(rates, dict):
            source = rates.items()
        elif isinstance(rates, list):
            source = ((item.get("key") if isinstance(item, dict) else "", item) for item in rates)
        else:
            return []
        records: list[dict[str, Any]] = []
        for raw_key, raw_item in source:
            if not isinstance(raw_item, dict):
                continue
            key = str(raw_item.get("key", raw_key) or "").strip()
            if not key:
                continue
            records.append({
                "key": key,
                "label": str(raw_item.get("label") or key),
                "total": PersistentStatsService._positive_finite(raw_item.get("total", raw_item.get("tokens"))),
                "samples": PersistentStatsService._positive_finite(raw_item.get("samples")),
                "tokens": PersistentStatsService._positive_finite(raw_item.get("tokens", raw_item.get("total"))),
                "seconds": PersistentStatsService._positive_finite(raw_item.get("seconds")),
                "source": str(raw_item.get("source") or ""),
            })
        return records

    @staticmethod
    def _recalculate_agent_token_totals(bucket: dict[str, Any]) -> None:
        canonical_rates: dict[str, dict[str, Any]] = {}
        token_total = 0.0
        sample_total = 0.0
        for item in PersistentStatsService._agent_token_rate_records(bucket.get("agent_token_rates")):
            key = item["key"]
            canonical_rates[key] = {
                "label": item["label"],
                "total": float(item.get("total") or 0.0),
                "samples": float(item.get("samples") or 0.0),
                "tokens": float(item.get("tokens") or 0.0),
                "seconds": float(item.get("seconds") or 0.0),
                "source": str(item.get("source") or ""),
            }
            token_total += canonical_rates[key]["tokens"]
            sample_total += canonical_rates[key]["samples"]
        bucket["agent_token_rates"] = canonical_rates
        bucket["tokens_per_agent_total"] = token_total
        bucket["agent_token_samples"] = sample_total

    def recover_agent_token_history(self, records: list[dict[str, Any]], *, now: float | None = None) -> dict[str, Any]:
        """Import one transcript-derived token timeline without rewriting JSON status."""

        if self.store.metadata_value(STATSD_AGENT_TOKEN_RECOVERY_MARKER) == str(STATSD_AGENT_TOKEN_RECOVERY_VERSION):
            return {"ok": True, "changed": False, "reason": "already_recovered"}
        sample_now = float(time.time() if now is None else now)
        if not math.isfinite(sample_now):
            raise ValueError("now must be finite")
        recovered: dict[tuple[int, int], dict[str, Any]] = {}
        for record in records:
            if not isinstance(record, dict):
                continue
            try:
                sample_time = float(record.get("time") or 0.0)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(sample_time) or sample_time < sample_now - STATS_HISTORY_RETENTION_SECONDS:
                continue
            duration = self._bucket_seconds(sample_time, sample_now)
            start = int(math.floor(sample_time / duration) * duration)
            bucket = recovered.setdefault((start, duration), stats_store.empty_bucket(start, duration))
            target_rates = bucket.setdefault("agent_token_rates", {})
            for item in self._agent_token_rate_records(record.get("agent_token_rates")):
                target = target_rates.setdefault(item["key"], {"label": item["label"], "total": 0.0, "samples": 0.0, "tokens": 0.0, "seconds": 0.0, "source": ""})
                for field in ("total", "samples", "tokens", "seconds"):
                    target[field] = float(target.get(field) or 0.0) + float(item.get(field) or 0.0)
                target["label"] = item["label"]
                if item.get("source"):
                    target["source"] = str(item["source"])
            self._recalculate_agent_token_totals(bucket)
        connection = self.store._connection()
        changed = False
        with connection:
            for key, recovered_bucket in recovered.items():
                existing = self.store.bucket(*key) or stats_store.empty_bucket(*key)
                target_rates = existing.setdefault("agent_token_rates", {})
                bucket_changed = False
                for item in self._agent_token_rate_records(recovered_bucket.get("agent_token_rates")):
                    if item["key"] in target_rates:
                        continue
                    target_rates[item["key"]] = {
                        "label": item["label"],
                        "total": float(item.get("total") or 0.0),
                        "samples": float(item.get("samples") or 0.0),
                        "tokens": float(item.get("tokens") or 0.0),
                        "seconds": float(item.get("seconds") or 0.0),
                        "source": str(item.get("source") or "transcript"),
                    }
                    bucket_changed = True
                if bucket_changed:
                    next_sequence = int(self.store.diagnostics().get("sequence") or 0) + 1
                    existing["server_sequence"] = max(int(existing.get("server_sequence") or 0), next_sequence)
                    existing["sequence"] = max(int(existing.get("sequence") or 0), next_sequence)
                    self._recalculate_agent_token_totals(existing)
                    self.store._upsert_bucket(connection, existing)
                    changed = True
            connection.execute(
                "INSERT INTO schema_meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (STATSD_AGENT_TOKEN_RECOVERY_MARKER, str(STATSD_AGENT_TOKEN_RECOVERY_VERSION)),
            )
        if changed:
            self._encoded_query_cache.clear()
        return {"ok": True, "changed": changed, "recovered_buckets": len(recovered)}

    def recover_agent_token_history_from_rows(self, rows: list[dict[str, Any]], *, now: float | None = None) -> dict[str, Any]:
        sample_now = float(time.time() if now is None else now)
        recovery_start = sample_now - STATS_HISTORY_RETENTION_SECONDS
        records: list[dict[str, Any]] = []
        seen_keys: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            key = str(row.get("key") or "").strip()
            if not key or key in seen_keys:
                continue
            seen_keys.add(key)
            transcript = str(row.get("transcript") or "").strip()
            kind = str(row.get("kind") or "").strip().lower()
            if not transcript or kind not in {"claude", "codex"}:
                continue
            label = str(row.get("label") or key)
            previous_by_source: dict[str, float] = {}
            for event in session_files.transcript_generated_token_events(Path(transcript), kind):
                previous = previous_by_source.get(event.source)
                interval_start = previous if previous is not None else event.timestamp - STATS_AGENT_TOKEN_BUCKET_SECONDS
                previous_by_source[event.source] = event.timestamp
                interval_end = event.timestamp
                if interval_end <= interval_start or interval_end <= recovery_start or interval_start >= sample_now:
                    continue
                clipped_start = max(interval_start, recovery_start)
                clipped_end = min(interval_end, sample_now)
                if clipped_end <= clipped_start:
                    continue
                covered_fraction = (clipped_end - clipped_start) / (interval_end - interval_start)
                records.extend(self._agent_token_delta_records(
                    key,
                    label,
                    clipped_start,
                    clipped_end,
                    event.tokens * covered_fraction,
                ))
        return self.recover_agent_token_history(records, now=sample_now)

    def _compact_history(self, now: float) -> None:
        """Apply the bounded legacy retention tiers inside the durable owner."""
        sources = self.store.query_buckets(limit=stats_store.STATS_STORE_MAX_ROWS_PER_QUERY)
        compacted: dict[tuple[int, int], dict[str, Any]] = {}
        cutoff = now - STATS_HISTORY_RETENTION_SECONDS
        changed = False
        for source in sources:
            start = int(source.get("start") or 0)
            duration = int(source.get("duration") or 0)
            if start <= 0 or duration <= 0 or start < cutoff:
                changed = True
                continue
            target_duration = max(duration, self._bucket_seconds(float(start), now))
            target_start = int(math.floor(start / target_duration) * target_duration)
            key = (target_start, target_duration)
            target = compacted.setdefault(key, stats_store.empty_bucket(*key))
            self._merge_bucket(target, source)
            changed = changed or key != (start, duration)
        if changed:
            self.store.replace_buckets([compacted[key] for key in sorted(compacted)])

    def import_legacy_history_once(self, state_dir: Path | None = None) -> dict[str, Any]:
        """Import the legacy v4 snapshots once before public stats cutover."""
        if self.store.metadata_value(STATSD_LEGACY_IMPORT_MARKER) == str(STATSD_LEGACY_IMPORT_VERSION):
            return {"ok": True, "imported": False, "reason": "already_imported", "rows": self.store.diagnostics()["rows"]}
        existing_rows = self.store.diagnostics()["rows"]
        if existing_rows:
            self.store.set_metadata_value(STATSD_LEGACY_IMPORT_MARKER, str(STATSD_LEGACY_IMPORT_VERSION))
            return {"ok": True, "imported": False, "reason": "existing_statsd_history", "rows": existing_rows}
        root = Path(state_dir or self.store.path.parent)
        buckets: dict[tuple[int, int], dict[str, Any]] = {}
        client_bucket_keys: set[tuple[int, int]] = set()
        for path in default_legacy_client_history_paths(root):
            if not path.is_file():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            try:
                version = int(payload.get("version") or 0)
            except (TypeError, ValueError):
                continue
            if version not in (3, 4):
                continue
            for field, decoder in (("raw_buckets", self._legacy_client_bucket), ("rollup_buckets", self._legacy_client_bucket)):
                for snapshot in payload.get(field, []) if isinstance(payload.get(field), list) else []:
                    bucket = decoder(snapshot)
                    if bucket is not None:
                        key = (bucket["start"], bucket["duration"])
                        if key in client_bucket_keys:
                            continue
                        buckets[key] = bucket
                        client_bucket_keys.add(key)
        shared_path = default_legacy_shared_history_path(root)
        if shared_path.is_file():
            try:
                shared_payload = json.loads(shared_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                shared_payload = None
            stats_history = shared_payload.get("stats_history") if isinstance(shared_payload, dict) else None
            if isinstance(stats_history, dict):
                try:
                    shared_agent_token_schema_version = int(stats_history.get("agent_token_schema_version") or 0)
                except (TypeError, ValueError):
                    shared_agent_token_schema_version = 0
                for field in ("raw_buckets", "rollup_buckets"):
                    for snapshot in stats_history.get(field, []) if isinstance(stats_history.get(field), list) else []:
                        bucket = self._legacy_shared_bucket(snapshot)
                        if bucket is not None:
                            if shared_agent_token_schema_version < STATS_AGENT_TOKEN_SCHEMA_VERSION:
                                self._normalize_legacy_agent_token_intervals(bucket)
                            key = (bucket["start"], bucket["duration"])
                            existing = buckets.get(key, stats_store.empty_bucket(*key))
                            stats_store.merge_bucket(existing, bucket)
                            buckets[key] = existing
        self.store.replace_buckets_with_marker(
            [buckets[key] for key in sorted(buckets)],
            STATSD_LEGACY_IMPORT_MARKER,
            str(STATSD_LEGACY_IMPORT_VERSION),
        )
        self._encoded_query_cache.clear()
        return {"ok": True, "imported": True, "rows": len(buckets)}

    @staticmethod
    def _record_from_bucket(bucket: dict[str, Any], client_id: str = "", *, include_agent_tokens: bool = True) -> dict[str, Any]:
        clients = bucket.get("clients") if isinstance(bucket.get("clients"), dict) else {}
        clean_requested_client_id = stats_history_client_id(client_id)
        client = clients.get(clean_requested_client_id) if clean_requested_client_id else clients.get("")
        if not isinstance(client, dict):
            client = {}
        clean_clients: dict[str, dict[str, Any]] = {}
        for raw_client_id, values in sorted(clients.items()):
            clean_client_id = stats_history_client_id(raw_client_id)
            if not clean_client_id or not isinstance(values, dict):
                continue
            clean_clients[clean_client_id] = {
                **{field: float(values.get(field) or 0.0) for field in stats_store.BROWSER_FIELDS},
            }
        servers: dict[str, dict[str, Any]] = {}
        raw_servers = bucket.get("servers") if isinstance(bucket.get("servers"), dict) else {}
        for raw_process_id, values in sorted(raw_servers.items()):
            process_id = str(raw_process_id or "").strip()
            if not process_id or not isinstance(values, dict):
                continue
            servers[process_id] = {
                "label": str(values.get("label") or process_id),
                "pid": int(values.get("pid") or 0),
                "port": int(values.get("port") or 0),
                "started_at": float(values.get("started_at") or 0.0),
                "cpu_total_percent": float(values.get("cpu_total_percent") or 0.0),
                "cpu_count": float(values.get("cpu_count") or 0.0),
            }
        record = {
            "start": int(bucket.get("start") or 0),
            "duration": int(bucket.get("duration") or 0),
            "sequence": int(bucket.get("sequence") or 0),
            "clients": clean_clients,
            "servers": servers,
            **{field: float(client.get(field) or 0.0) for field in stats_store.BROWSER_FIELDS},
            **{
                field: float(bucket.get(field) or 0.0)
                for field in stats_store.SERVER_FIELDS
                if include_agent_tokens or field not in STATS_AGENT_TOKEN_HISTORY_FIELDS
            },
        }
        if include_agent_tokens:
            record["agent_token_rates"] = [
                {
                    "key": key,
                    "label": str(value.get("label") or key),
                    "total": float(value.get("total") or 0.0),
                    "samples": float(value.get("samples") or 0.0),
                    "tokens": float(value.get("tokens") or 0.0),
                    "seconds": float(value.get("seconds") or 0.0),
                    "source": str(value.get("source") or ""),
                }
                for key, value in sorted((bucket.get("agent_token_rates") or {}).items())
                if isinstance(value, dict)
            ]
            record["host_metrics"] = copy.deepcopy(bucket.get("host_metrics") if isinstance(bucket.get("host_metrics"), dict) else stats_store.empty_host_metrics())
        return record

    @staticmethod
    def _merge_bucket(target: dict[str, Any], source: dict[str, Any]) -> None:
        for field in stats_store.SERVER_FIELDS:
            target[field] = float(target.get(field) or 0.0) + float(source.get(field) or 0.0)
        target["sequence"] = max(int(target.get("sequence") or 0), int(source.get("sequence") or 0), int(source.get("server_sequence") or 0))
        target["server_sequence"] = max(int(target.get("server_sequence") or 0), int(source.get("server_sequence") or 0))
        for mapping_key in ("clients", "servers"):
            target_mapping = target.setdefault(mapping_key, {})
            source_mapping = source.get(mapping_key) if isinstance(source.get(mapping_key), dict) else {}
            for identity, values in source_mapping.items():
                existing = target_mapping.setdefault(identity, {})
                if isinstance(values, dict):
                    for field, value in values.items():
                        if isinstance(value, (int, float)) and field not in {"sequence", "pid", "port", "started_at"}:
                            existing[field] = float(existing.get(field) or 0.0) + float(value)
                        elif field not in existing or value:
                            existing[field] = value
        target_rates = target.setdefault("agent_token_rates", {})
        source_rates = source.get("agent_token_rates") if isinstance(source.get("agent_token_rates"), dict) else {}
        for rate_key, values in source_rates.items():
            if not isinstance(values, dict):
                continue
            existing = target_rates.setdefault(str(rate_key), {"label": str(values.get("label") or rate_key), "total": 0.0, "samples": 0.0, "tokens": 0.0, "seconds": 0.0, "source": ""})
            for field in ("total", "samples", "tokens", "seconds"):
                existing[field] = float(existing.get(field) or 0.0) + float(values.get(field) or 0.0)
            existing["label"] = str(values.get("label") or existing.get("label") or rate_key)
            if values.get("source"):
                existing["source"] = str(values["source"])
        source_metrics = source.get("host_metrics") if isinstance(source.get("host_metrics"), dict) else {}
        if source_metrics:
            target_metrics = target.setdefault("host_metrics", stats_store.empty_host_metrics())
            for field in ("cpu_label", "system_memory_label"):
                if source_metrics.get(field):
                    target_metrics[field] = str(source_metrics[field])
            for field in ("system_memory_used_total_bytes", "system_memory_capacity_total_bytes", "system_memory_count"):
                target_metrics[field] = float(target_metrics.get(field) or 0.0) + float(source_metrics.get(field) or 0.0)
            for mapping_field in ("cpu_processes", "memory_processes", "gpu_util_processes", "gpu_memory_processes", "gpu_devices"):
                source_items = source_metrics.get(mapping_field) if isinstance(source_metrics.get(mapping_field), dict) else {}
                target_items = target_metrics.setdefault(mapping_field, {})
                for item_key, source_item in source_items.items():
                    if not isinstance(source_item, dict):
                        continue
                    item = target_items.setdefault(str(item_key), {"label": str(source_item.get("label") or item_key)})
                    item["label"] = str(source_item.get("label") or item.get("label") or item_key)
                    for field, value in source_item.items():
                        if field == "label":
                            continue
                        if isinstance(value, (int, float)):
                            item[field] = float(item.get(field) or 0.0) + float(value)

    def _encoded_history(self, request: dict[str, Any]) -> dict[str, Any]:
        after_sequence = max(0, int(request.get("after_sequence", request.get("since", 0)) or 0))
        start = max(0, int(request.get("start") or 0))
        end = max(0, int(request.get("end") or 0))
        resolution = max(0, int(request.get("resolution_seconds") or 0))
        max_points = max(0, int(request.get("max_points") or 0))
        client_id = stats_history_client_id(request.get("client_id") or "")
        token_resolution = max(0, int(request.get("token_resolution_seconds", request.get("token_resolution", 0)) or 0))
        include_agent_tokens = bool(request.get("include_agent_tokens", token_resolution <= 0))
        generation = int(self.store.diagnostics().get("sequence") or 0)
        include_token_history = bool(int(request.get("token_resolution_seconds", request.get("token_resolution", 0)) or 0))
        token_since = max(0, int(request.get("token_since") or 0))
        token_history_start = max(0, int(request.get("token_history_start", start) if request.get("token_history_start") is not None else start))
        token_history_end = max(0, int(request.get("token_history_end", end) if request.get("token_history_end") is not None else end))
        cache_key = (
            after_sequence,
            start,
            end,
            resolution,
            max_points,
            client_id,
            include_agent_tokens,
            include_token_history,
            token_since,
            token_resolution,
            token_history_start,
            token_history_end,
            generation,
        )
        cached = self._encoded_query_cache.get(cache_key)
        if cached is not None and time.monotonic() - cached[1] <= self._query_cache_ttl_seconds:
            return copy.deepcopy(cached[0])
        # A live cursor is immutable: records before it cannot change the
        # returned delta. Range/zoom reads need the full bounded window to
        # aggregate exact totals at the requested resolution.
        coverage_facts = self.store.query_coverage(start=start, end=end)
        source_buckets = self.store.query_buckets(after_sequence=after_sequence if not end else 0, start=start, end=end)
        available_start = coverage_facts["available_start"]
        available_end = coverage_facts["available_end"]
        retained_resolution = coverage_facts["retained_resolution"]
        effective_resolution = max(resolution, retained_resolution)
        if max_points and source_buckets:
            span = max(1, (end or available_end) - (start or available_start))
            effective_resolution = max(effective_resolution, math.ceil(span / max_points))
        def encode_records(target_resolution: int) -> list[dict[str, Any]]:
            grouped: dict[tuple[int, int], dict[str, Any]] = {}
            for bucket in source_buckets:
                bucket_start, bucket_duration = int(bucket["start"]), int(bucket["duration"])
                if not target_resolution or bucket_duration >= target_resolution:
                    bucket_key = (bucket_start, bucket_duration)
                else:
                    anchor = start or 0
                    bucket_key = (anchor + ((bucket_start - anchor) // target_resolution) * target_resolution, target_resolution)
                target = grouped.setdefault(bucket_key, stats_store.empty_bucket(*bucket_key))
                self._merge_bucket(target, bucket)
            return [
                self._record_from_bucket(bucket, client_id, include_agent_tokens=include_agent_tokens)
                for _key, bucket in sorted(grouped.items())
                if int(bucket.get("sequence") or 0) > after_sequence
            ]

        records = encode_records(effective_resolution)
        while max_points and len(records) > max_points:
            effective_resolution = max(effective_resolution + 1, math.ceil(effective_resolution * len(records) / max_points))
            records = encode_records(effective_resolution)
        covered_start = max(start or available_start, available_start) if available_start else 0
        covered_end = min(end or available_end, available_end) if available_end else 0
        bounded_older = bool(end)
        coverage = {
            "mode": "older" if bounded_older else "live",
            "requested_start": start,
            "requested_end": end,
            "available_start": available_start,
            "available_end": available_end,
            "covered_start": covered_start,
            "covered_end": covered_end,
            "complete": bool(covered_start and covered_end and (not start or covered_start <= start) and (not end or covered_end >= end)),
            "has_more_older": bool(available_start and covered_start and available_start < covered_start),
            "next_older_end": covered_start if available_start and covered_start and available_start < covered_start else 0,
            "resolution_seconds": effective_resolution,
            "source_resolution_seconds": retained_resolution,
            "max_points": max_points,
            "source_records": coverage_facts["source_records"],
            "returned_records": len(records),
            "cursor": after_sequence if bounded_older else generation,
            "latest_cursor": generation,
        }
        payload = {
            "sequence": coverage["cursor"],
            "latest_sequence": generation,
            "agent_token_schema_version": STATS_AGENT_TOKEN_SCHEMA_VERSION,
            "records": records,
            "coverage": coverage,
            "retention_seconds": STATS_HISTORY_RETENTION_SECONDS,
            "raw_window_seconds": STATS_HISTORY_RAW_WINDOW_SECONDS,
            "middle_window_seconds": STATS_HISTORY_MIDDLE_WINDOW_SECONDS,
            "middle_bucket_seconds": 10,
            "rollup_bucket_seconds": STATS_HISTORY_ROLLUP_BUCKET_SECONDS,
            "tiers": [{"max_age_seconds": max_age, "bucket_seconds": duration} for max_age, duration in STATS_HISTORY_TIERS],
            "client_id": client_id,
        }
        if token_resolution:
            token_request = {
                "after_sequence": token_since,
                "start": token_history_start,
                "end": token_history_end,
                "resolution_seconds": max(STATS_AGENT_TOKEN_BUCKET_SECONDS, token_resolution),
                "max_points": max_points,
                "client_id": client_id,
                "include_agent_tokens": True,
            }
            token_payload = self._encoded_history(token_request)
            payload["agent_token_history"] = {
                "sequence": token_payload["sequence"],
                "latest_sequence": token_payload["latest_sequence"],
                "records": [
                    {key: record[key] for key in ("start", "duration", "sequence", "tokens_per_agent_total", "agent_token_samples", "agent_token_rates")}
                    for record in token_payload["records"]
                ],
                "resolution_seconds": token_request["resolution_seconds"],
                "snapshot": token_request["after_sequence"] == 0 and token_history_end <= 0,
                "coverage": token_payload["coverage"],
            }
        self._encoded_query_cache = {cache_key: (copy.deepcopy(payload), time.monotonic())}
        return payload

    def encoded_history_from_buckets(self, buckets: list[dict[str, Any]], request: dict[str, Any]) -> bytes:
        """Encode a bounded legacy snapshot without making the caller serialize it."""
        self.store.replace_buckets(buckets)
        self._encoded_query_cache.clear()
        return json.dumps(self._encoded_history(request), ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    def encoded_sample(self, sample: dict[str, Any], shared_stats: dict[str, Any], query: dict[str, Any]) -> bytes:
        """Encode the complete public stats response without an HTTP round-trip through Python objects."""
        payload: dict[str, Any] = {"ok": True}
        payload.update(sample)
        payload["history"] = self._encoded_history(query)
        payload["shared_stats"] = shared_stats
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    def common_status(self) -> dict[str, Any]:
        try:
            cache = self.store.diagnostics()
            status = {"database": str(self.store.path)}
            generation = int(cache.get("sequence") or 0)
            last_failure = self.last_sampler_failure
        except sqlite3.Error as exc:
            cache = {"error": str(exc), "rows": 0, "sequence": 0}
            status = {"database": str(self.store.path), "error": str(exc)}
            generation = 0
            last_failure = str(exc)
        return {
            "ok": True,
            "version": STATSD_PROTOCOL_VERSION,
            "pid": os.getpid(),
            "started_at": self.started_at,
            "socket": str(self.socket_path),
            "clients": len(self.leases),
            "queues": {"interactive": 0, "normal": 0, "maintenance": 0},
            "active_task": "",
            "cache": cache,
            "last_success": self.last_client_at,
            "last_failure": last_failure,
            "last_sampler_success_at": self.last_sampler_success_at,
            "last_sampler_attempt_at": self.last_sampler_attempt_at,
            "restart_backoff_seconds": 0.0,
            "generation": generation,
            "idle_seconds": self.idle_seconds,
            "status": status,
        }

    def handle_with_binary(self, request: dict[str, Any]) -> tuple[dict[str, Any], bytes]:
        """Return response metadata plus optional pre-encoded JSON bytes.

        ``write_encoded_history`` is the only RPC action that returns a binary
        payload.  This keeps cached history opaque after statsd encodes it;
        HTTP still owns compression, HEAD, auth cookies, and response metrics.
        """
        action = str(request.get("action") or "")
        if action == "ping":
            return {"ok": True, "version": STATSD_PROTOCOL_VERSION, "pid": os.getpid(), "started_at": self.started_at}, b""
        if action == "status":
            return self.common_status(), b""
        if action == "profile":
            return {"ok": True, "profile": self.common_status()}, b""
        if action == "lease":
            response = acquire_client_lease(self.leases, request.get("client_pid"))
            return {**response, "version": STATSD_PROTOCOL_VERSION}, b""
        if action == "release":
            return release_client_lease(self.leases, request.get("lease_id")), b""
        if action == "shutdown_if_idle":
            if self.leases:
                return {"ok": True, "shutdown": False, "leases": len(self.leases)}, b""
            self.stop_event.set()
            return {"ok": True, "shutdown": True}, b""
        if action == "shutdown":
            self.stop_event.set()
            self.sampler_wake_event.set()
            return {"ok": True}, b""
        if action == "set_sampler_owner":
            owner = request.get("owner")
            if not isinstance(owner, dict) or not isinstance(owner.get("control_socket"), str) or not owner["control_socket"]:
                return {"ok": False, "error": "owner control socket is required"}, b""
            self.sampler_owner = dict(owner)
            self.sampler_wake_event.set()
            return {"ok": True, "owner": {"port": int(owner.get("port") or 0), "control_socket": owner["control_socket"]}}, b""
        if action == "set_token_consumer_until":
            try:
                consumer_until = max(0.0, float(request.get("consumer_until") or 0.0))
            except (TypeError, ValueError):
                return {"ok": False, "error": "consumer_until must be a number"}, b""
            self.agent_token_consumer_until = max(self.agent_token_consumer_until, consumer_until)
            self.sampler_wake_event.set()
            return {"ok": True, "agent_token_consumer_until": self.agent_token_consumer_until}, b""
        if action == "mark_sampler_success":
            try:
                sample_time = max(0.0, float(request.get("sample_time") or time.time()))
            except (TypeError, ValueError):
                sample_time = time.time()
            self.last_sampler_attempt_at = sample_time
            self.last_sampler_success_at = sample_time
            self.last_sampler_failure = ""
            return {"ok": True, "last_sampler_success_at": self.last_sampler_success_at}, b""
        if action == "upsert_bucket":
            bucket = request.get("bucket")
            if not isinstance(bucket, dict):
                return {"ok": False, "error": "bucket must be an object"}, b""
            try:
                self.store.upsert_bucket(bucket)
            except (TypeError, ValueError, sqlite3.Error) as exc:
                return {"ok": False, "error": str(exc)}, b""
            self._encoded_query_cache.clear()
            return {"ok": True, "status": self.store.diagnostics()}, b""
        if action == "merge_records":
            records = request.get("records")
            if not isinstance(records, list):
                return {"ok": False, "error": "records must be a list"}, b""
            try:
                return self.merge_records(
                    [record for record in records if isinstance(record, dict)],
                    client_id=str(request.get("client_id") or ""),
                    now=request.get("now"),
                    clear=bool(request.get("clear")),
                ), b""
            except (TypeError, ValueError, sqlite3.Error) as exc:
                return {"ok": False, "error": str(exc)}, b""
        if action == "merge_and_history":
            records = request.get("records")
            query = request.get("query")
            if not isinstance(records, list) or not isinstance(query, dict):
                return {"ok": False, "error": "records and query are required"}, b""
            try:
                merged = self.merge_records(
                    [record for record in records if isinstance(record, dict)],
                    client_id=str(request.get("client_id") or ""),
                    now=request.get("now"),
                    clear=bool(request.get("clear")),
                )
                history = self._encoded_history(query)
            except (TypeError, ValueError, sqlite3.Error) as exc:
                return {"ok": False, "error": str(exc)}, b""
            return {"ok": True, "merged": merged, "history": history}, b""
        if action == "merge_server_records":
            records = request.get("records")
            if not isinstance(records, list):
                return {"ok": False, "error": "records must be a list"}, b""
            try:
                return self.merge_server_records([record for record in records if isinstance(record, dict)], now=request.get("now")), b""
            except (TypeError, ValueError, sqlite3.Error) as exc:
                return {"ok": False, "error": str(exc)}, b""
        if action == "claim_agent_token_deltas":
            measurements = request.get("measurements")
            seen_keys = request.get("seen_keys")
            if not isinstance(measurements, list) or not isinstance(seen_keys, list):
                return {"ok": False, "error": "measurements and seen_keys are required"}, b""
            try:
                return self.claim_agent_token_deltas(
                    [item for item in measurements if isinstance(item, dict)],
                    {str(key) for key in seen_keys},
                    float(request.get("sample_time") or 0.0),
                    fallback_state=request.get("fallback_state"),
                ), b""
            except (TypeError, ValueError, sqlite3.Error) as exc:
                return {"ok": False, "error": str(exc)}, b""
        if action == "claim_agent_token_deltas_from_rows":
            rows = request.get("rows")
            seen_keys = request.get("seen_keys")
            if not isinstance(rows, list) or not isinstance(seen_keys, list):
                return {"ok": False, "error": "rows and seen_keys are required"}, b""
            try:
                return self.claim_agent_token_deltas_from_rows(
                    [item for item in rows if isinstance(item, dict)],
                    {str(key) for key in seen_keys},
                    float(request.get("sample_time") or 0.0),
                    fallback_state=request.get("fallback_state"),
                ), b""
            except (TypeError, ValueError, sqlite3.Error, OSError) as exc:
                return {"ok": False, "error": str(exc)}, b""
        if action == "recover_agent_token_history":
            records = request.get("records")
            if not isinstance(records, list):
                return {"ok": False, "error": "records must be a list"}, b""
            try:
                return self.recover_agent_token_history([record for record in records if isinstance(record, dict)], now=request.get("now")), b""
            except (TypeError, ValueError, sqlite3.Error) as exc:
                return {"ok": False, "error": str(exc)}, b""
        if action == "recover_agent_token_history_from_rows":
            rows = request.get("rows")
            if not isinstance(rows, list):
                return {"ok": False, "error": "rows must be a list"}, b""
            try:
                return self.recover_agent_token_history_from_rows([row for row in rows if isinstance(row, dict)], now=request.get("now")), b""
            except (TypeError, ValueError, sqlite3.Error, OSError) as exc:
                return {"ok": False, "error": str(exc)}, b""
        if action == "replace_buckets":
            buckets = request.get("buckets")
            if not isinstance(buckets, list):
                return {"ok": False, "error": "buckets must be a list"}, b""
            try:
                self.store.replace_buckets([bucket for bucket in buckets if isinstance(bucket, dict)])
            except (TypeError, ValueError, sqlite3.Error) as exc:
                return {"ok": False, "error": str(exc)}, b""
            self._encoded_query_cache.clear()
            return {"ok": True, "status": self.store.diagnostics()}, b""
        if action == "query_buckets":
            try:
                buckets = self.store.query_buckets(
                    after_sequence=int(request.get("after_sequence") or 0),
                    start=int(request.get("start") or 0),
                    end=int(request.get("end") or 0),
                    limit=int(request.get("limit") or 0) or 2000,
                )
            except (TypeError, ValueError, sqlite3.Error) as exc:
                return {"ok": False, "error": str(exc)}, b""
            return {"ok": True, "buckets": buckets, "status": self.store.diagnostics()}, b""
        if action == "write_encoded_history":
            try:
                encoded = json.dumps(self._encoded_history(request), sort_keys=True, separators=(",", ":")).encode("utf-8")
            except (TypeError, ValueError, sqlite3.Error) as exc:
                return {"ok": False, "error": str(exc)}, b""
            return {"ok": True, "encoding": "json", "size": len(encoded)}, encoded
        if action == "write_encoded_sample":
            sample = request.get("sample")
            shared_stats = request.get("shared_stats")
            query = request.get("query")
            if not isinstance(sample, dict) or not isinstance(shared_stats, dict) or not isinstance(query, dict):
                return {"ok": False, "error": "sample, shared_stats, and query are required"}, b""
            try:
                encoded = self.encoded_sample(sample, shared_stats, query)
            except (TypeError, ValueError, sqlite3.Error) as exc:
                return {"ok": False, "error": str(exc)}, b""
            return {"ok": True, "encoding": "json", "size": len(encoded)}, encoded
        if action == "replace_and_write_encoded_history":
            buckets = request.get("buckets")
            query = request.get("query")
            if not isinstance(buckets, list) or not isinstance(query, dict):
                return {"ok": False, "error": "buckets and query are required"}, b""
            try:
                encoded = self.encoded_history_from_buckets([bucket for bucket in buckets if isinstance(bucket, dict)], query)
            except (TypeError, ValueError, sqlite3.Error) as exc:
                return {"ok": False, "error": str(exc)}, b""
            return {"ok": True, "encoding": "json", "size": len(encoded)}, encoded
        if action == "history":
            try:
                return {"ok": True, **self._encoded_history(request)}, b""
            except (TypeError, ValueError, sqlite3.Error) as exc:
                return {"ok": False, "error": str(exc)}, b""
        if action == "retain_after":
            try:
                deleted = self.store.retain_after(float(request.get("cutoff_time") or 0.0))
            except (TypeError, ValueError, sqlite3.Error) as exc:
                return {"ok": False, "error": str(exc)}, b""
            self._encoded_query_cache.clear()
            return {"ok": True, "deleted": deleted, "status": self.store.diagnostics()}, b""
        return {"ok": False, "error": f"unknown stats action: {action}"}, b""

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        """Compatibility helper for direct service tests and legacy callers."""
        response, binary = self.handle_with_binary(request)
        if binary:
            response = {**response, "bytes": binary.decode("utf-8")}
        return response

    def run(self) -> int:
        self.import_legacy_history_once()
        self.sampler_thread = threading.Thread(target=self._sampler_loop, name="statsd-sampler", daemon=True)
        self.sampler_thread.start()
        return run_local_rpc_service(
            socket_path=self.socket_path,
            lock_path=self.lock_path,
            service_name="statsd",
            stop_event=self.stop_event,
            handle=self.handle_with_binary,
            on_idle=lambda: not self.leases and monotonic_clock() - self.last_client_at >= self.idle_seconds,
            on_client=lambda: setattr(self, "last_client_at", monotonic_clock()),
            on_shutdown=self._shutdown,
        )

    def _shutdown(self) -> None:
        self.stop_event.set()
        self.sampler_wake_event.set()
        if self.sampler_thread is not None and self.sampler_thread.is_alive():
            self.sampler_thread.join(timeout=1.0)
        self.store.close()


class StatsClient(LocalServiceClient):
    """Thin cross-port client for the ``statsd`` durable owner."""

    def __init__(self, socket_path: Path | None = None, database_path: Path | None = None):
        self.database_path = Path(database_path or default_database_path())
        super().__init__(
            "statsd",
            "yolomux_lib.statsd",
            socket_path or default_socket_path(),
            STATSD_PROTOCOL_VERSION,
            idle_seconds=STATSD_DEFAULT_IDLE_SECONDS,
            extra_args=("--database", str(self.database_path)),
        )

    def healthy(self) -> bool:
        response = self.request({"action": "ping"}, timeout=0.15)
        return bool(response.get("ok")) and int(response.get("version") or 0) == STATSD_PROTOCOL_VERSION

    def runtime_status(self) -> dict[str, Any]:
        status = self.registry.status()
        payload = status.get("status") if isinstance(status.get("status"), dict) else {}
        return {
            "service": "statsd", "pid": int(payload.get("pid") or 0), "started_at": float(payload.get("started_at") or 0.0),
            "version": int(payload.get("version") or 0), "socket": str(payload.get("socket") or self.socket_path),
            "healthy": bool(status.get("healthy")), "clients": int(payload.get("clients") or 0),
            "queues": payload.get("queues") if isinstance(payload.get("queues"), dict) else {},
            "active_task": str(payload.get("active_task") or ""), "cache": payload.get("cache") if isinstance(payload.get("cache"), dict) else {},
            "last_success": float(payload.get("last_success") or 0.0), "last_failure": str(payload.get("last_failure") or ""),
            "last_sampler_success_at": float(payload.get("last_sampler_success_at") or 0.0),
            "last_sampler_attempt_at": float(payload.get("last_sampler_attempt_at") or 0.0),
            "restart_backoff_seconds": max(0.0, float(status.get("next_start_at") or 0.0) - monotonic_clock()),
            "generation": int(payload.get("generation") or 0), "record": status.get("record") if isinstance(status.get("record"), dict) else {},
            "resources": self.registry.resources(int(payload.get("pid") or 0)),
        }

    def history(self, **request: Any) -> dict[str, Any]:
        if not self.ensure_started():
            return {"ok": False, "error": "statsd unavailable"}
        response = self.request({"action": "history", **request}, timeout=1.0)
        if response.get("ok") is not False:
            return response
        if not self.ensure_started():
            return response
        return self.request({"action": "history", **request}, timeout=1.0)

    def merge_records(self, records: list[dict[str, Any]], *, client_id: str, now: float | None = None, clear: bool = False) -> dict[str, Any]:
        if not self.ensure_started():
            return {"ok": False, "error": "statsd unavailable"}
        return self.request({"action": "merge_records", "records": records, "client_id": client_id, "now": now, "clear": clear}, timeout=3.0)

    def merge_and_history(self, records: list[dict[str, Any]], *, client_id: str, query: dict[str, Any], now: float | None = None, clear: bool = False) -> dict[str, Any]:
        if not self.ensure_started():
            return {"ok": False, "error": "statsd unavailable"}
        return self.request(
            {"action": "merge_and_history", "records": records, "client_id": client_id, "query": query, "now": now, "clear": clear},
            timeout=3.0,
        )

    def merge_server_records(self, records: list[dict[str, Any]], *, now: float | None = None) -> dict[str, Any]:
        if not self.ensure_started():
            return {"ok": False, "error": "statsd unavailable"}
        return self.request({"action": "merge_server_records", "records": records, "now": now}, timeout=3.0)

    def claim_agent_token_deltas(
        self,
        measurements: list[dict[str, Any]],
        *,
        seen_keys: set[str],
        sample_time: float,
        fallback_state: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if not self.ensure_started():
            return {"ok": False, "error": "statsd unavailable"}
        return self.request(
            {
                "action": "claim_agent_token_deltas",
                "measurements": measurements,
                "seen_keys": sorted(seen_keys),
                "sample_time": sample_time,
                "fallback_state": fallback_state or {},
            },
            timeout=3.0,
        )

    def claim_agent_token_deltas_from_rows(
        self,
        rows: list[dict[str, Any]],
        *,
        seen_keys: set[str],
        sample_time: float,
        fallback_state: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if not self.ensure_started():
            return {"ok": False, "error": "statsd unavailable"}
        return self.request(
            {
                "action": "claim_agent_token_deltas_from_rows",
                "rows": rows,
                "seen_keys": sorted(seen_keys),
                "sample_time": sample_time,
                "fallback_state": fallback_state or {},
            },
            timeout=3.0,
        )

    def recover_agent_token_history(self, records: list[dict[str, Any]], *, now: float) -> dict[str, Any]:
        if not self.ensure_started():
            return {"ok": False, "error": "statsd unavailable"}
        return self.request({"action": "recover_agent_token_history", "records": records, "now": now}, timeout=3.0)

    def recover_agent_token_history_from_rows(self, rows: list[dict[str, Any]], *, now: float) -> dict[str, Any]:
        if not self.ensure_started():
            return {"ok": False, "error": "statsd unavailable"}
        return self.request({"action": "recover_agent_token_history_from_rows", "rows": rows, "now": now}, timeout=3.0)

    def set_sampler_owner(self, owner: dict[str, Any]) -> dict[str, Any]:
        if not self.ensure_started():
            return {"ok": False, "error": "statsd unavailable"}
        return self.request({"action": "set_sampler_owner", "owner": owner}, timeout=1.0)

    def set_token_consumer_until(self, consumer_until: float) -> dict[str, Any]:
        if not self.ensure_started():
            return {"ok": False, "error": "statsd unavailable"}
        return self.request({"action": "set_token_consumer_until", "consumer_until": consumer_until}, timeout=1.0)

    def mark_sampler_success(self, sample_time: float | None = None) -> dict[str, Any]:
        if not self.ensure_started():
            return {"ok": False, "error": "statsd unavailable"}
        return self.request({"action": "mark_sampler_success", "sample_time": sample_time}, timeout=1.0)

    def encoded_history(self, **request: Any) -> tuple[dict[str, Any], bytes]:
        if not self.ensure_started():
            return {"ok": False, "error": "statsd unavailable"}, b""
        return self.request_with_binary({"action": "write_encoded_history", **request})

    def encoded_sample(self, sample: dict[str, Any], shared_stats: dict[str, Any], *, query: dict[str, Any]) -> tuple[dict[str, Any], bytes]:
        if not self.ensure_started():
            return {"ok": False, "error": "statsd unavailable"}, b""
        return self.request_with_binary(
            {"action": "write_encoded_sample", "sample": sample, "shared_stats": shared_stats, "query": query}, timeout=3.0
        )

    def replace_and_encoded_history(self, buckets: list[dict[str, Any]], **query: Any) -> tuple[dict[str, Any], bytes]:
        if not self.ensure_started():
            return {"ok": False, "error": "statsd unavailable"}, b""
        return self.request_with_binary({"action": "replace_and_write_encoded_history", "buckets": buckets, "query": query}, timeout=3.0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="YOLOmux persistent stats service")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--socket", default=str(default_socket_path()))
    parser.add_argument("--database", default=str(default_database_path()))
    parser.add_argument("--idle-seconds", type=float, default=STATSD_DEFAULT_IDLE_SECONDS)
    args = parser.parse_args(argv)
    if not args.serve:
        parser.error("--serve is required")
    apply_service_process_priority()
    return PersistentStatsService(Path(args.socket), Path(args.database), idle_seconds=args.idle_seconds).run()


if __name__ == "__main__":
    raise SystemExit(main())
