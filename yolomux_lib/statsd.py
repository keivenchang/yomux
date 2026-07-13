"""Persistent single-writer YO!stats service.

This is introduced alongside the legacy in-process history owner.  It stores
the exact bucket shape and validates the service lifecycle before P6 redirects
the public endpoint to it, avoiding two normal writers during a rolling deploy.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import multiprocessing
import os
import sqlite3
import tempfile
import threading
import time
from datetime import datetime
from datetime import timezone
from decimal import Decimal
from decimal import ROUND_HALF_UP
from pathlib import Path
from time import monotonic as monotonic_clock
from typing import Any

from .common import STATE_DIR
from .local_services.rpc import LOCAL_RPC_VERSION
from .local_services.rpc import safe_socket_path
from .pricing_catalog import PricingCatalog
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
STATSD_PROTOCOL_VERSION = 21
STATSD_COMPAT_PROTOCOL_VERSION = 8
STATSD_DEFAULT_IDLE_SECONDS = 300.0
STATSD_SOCKET_NAME = "statsd.sock"
STATS_READER_SOCKET_NAME = "stats-reader.sock"
STATSD_DATABASE_NAME = "stats-history.sqlite3"
STATSD_LEGACY_IMPORT_VERSION = 1
STATSD_LEGACY_IMPORT_MARKER = "legacy_import_version"
STATSD_AGENT_TOKEN_STATE_KEY = "agent_token_state"
STATSD_AGENT_TOKEN_ATOM_STATE_KEY = "agent_token_atom_state"
STATSD_AGENT_TOKEN_ATOM_SPOOL_KEY = "agent_token_atom_spool"
STATSD_AGENT_TOKEN_RECOVERY_MARKER = "agent_token_history_recovery_version"
STATSD_AGENT_TOKEN_RECOVERY_VERSION = 6
STATSD_USAGE_ATOM_MIGRATION_MARKER = "usage_atom_migration_version"
STATSD_USAGE_ATOM_MIGRATION_STATUS_KEY = "usage_atom_migration_status"
STATSD_USAGE_ATOM_MIGRATION_VERSION = 1
STATSD_PRICING_REPROJECTION_MARKER = "pricing_reprojection_catalog_revision"
STATSD_PRICING_PROJECTION_POLICY_VERSION = 2
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
STATS_HISTORY_PERSISTED_ROLLUP_SECONDS = (10, 60, 300, 600)
STATSD_ROLLUP_MAINTENANCE_PAGE_ROWS = 4
STATSD_ROLLUP_MAINTENANCE_BUDGET_SECONDS = 0.08
STATS_AGENT_TOKEN_BUCKET_SECONDS = 60
STATS_AGENT_TOKEN_MAX_ATTRIBUTION_GAP_SECONDS = 180.0
STATS_AGENT_TOKEN_SCHEMA_VERSION = 4
STATS_AGENT_TOKEN_HISTORY_FIELDS = ("tokens_per_agent_total", "agent_token_samples")
STATS_COST_COMPONENT_DIMENSION_FIELDS = (
    "provider", "model", "model_evidence", "effort", "direction", "modality", "cache_role", "unit",
    "root_thread_id", "agent_thread_id", "parent_thread_id", "depth", "endpoint", "tool_name",
    "tmux_key", "tmux_label", "tmux_session", "tmux_window", "tmux_window_label", "agent_kind",
    "pricing_profile", "service_tier", "backfill_source", "telemetry_complete", "source", "priced",
    "catalog_revision", "source_url", "effective_from", "rate_usd", "rate_scale",
    "estimated", "estimate_rate_min_usd", "estimate_rate_max_usd", "estimate_rate_scale",
    "estimate_source_url", "estimate_catalog_revision",
)
STATS_HISTORY_CLIENT_ID_MAX_LENGTH = 96
STATS_HISTORY_POST_MAX_RECORDS = 1000
STATSD_BACKGROUND_OWNER_STALE_SECONDS = 10.0
STATS_COST_SUMMARY_MAX_COMPONENTS = stats_store.STATS_COST_SUMMARY_MAX_COMPONENTS
STATS_COST_SUMMARY_MAX_BYTES = stats_store.STATS_COST_SUMMARY_MAX_BYTES
STATS_USAGE_ATOM_MIGRATION_BATCH_RECORDS = 500
# Both maintenance paths run on the sole SQLite/RPC owner.  Keep a single
# durable record per turn: a large JSON payload can cost far more than a
# simple record count suggests on a memory-constrained host.
STATSD_PRICING_REPROJECTION_BATCH_BUCKETS = 1
STATSD_AGENT_TOKEN_PERSIST_BATCH_RECORDS = 1
# A page executes on statsd's sole SQLite/RPC owner. One atom is the bounded
# unit: a "record" may carry expensive pricing and summary projection work.
STATSD_AGENT_TOKEN_ATOM_PAGE_RECORDS = 1


def _scan_agent_token_rows_in_worker(
    rows: list[dict[str, Any]],
    sample_time: float,
    previous_state: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Parse transcript files without touching statsd's SQLite connection.

    This deliberately lives at module scope so the recovery worker can use a
    spawned process.  A thread only makes the SQLite owner asynchronous; a
    CPU-heavy JSONL parse still holds the same process GIL and delays the
    one-second sampler and RPC listener.
    """

    measurements: list[dict[str, Any]] = []
    atom_records: list[dict[str, Any]] = []
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
            "models": session_files.transcript_generated_tokens_by_model(transcript_path, kind),
        })
        # The old counter projection is deliberately retained above.  The
        # component stream is separate and idempotent by event identity, so it
        # cannot inflate the Output chart when both are present.
        previous_time = float(previous_state.get(key, {}).get("time") or sample_time)
        tmux_fields = _tmux_usage_fields_from_row(row, key=key, label=str(row.get("label") or key), kind=kind)
        for atom in session_files.transcript_usage_atoms(transcript_path, kind):
            if previous_time < atom.timestamp <= sample_time:
                # Dataclasses are useful inside the parser but the result file
                # is a wire format. Convert before crossing the process
                # boundary so an otherwise valid scan cannot wedge statsd.
                normalized = normalized_usage_atom(atom)
                if normalized is not None:
                    normalized.update(tmux_fields)
                    atom_records.append({"time": atom.timestamp, "usage_atoms": [normalized]})
    return measurements, atom_records


def _agent_token_spool_identity(atom: dict[str, Any]) -> str:
    """Return the same stable identity used by the durable bucket merger."""

    return json.dumps([
        str(atom.get("event_id") or ""),
        str(atom.get("direction") or ""),
        str(atom.get("modality") or ""),
        str(atom.get("cache_role") or ""),
        str(atom.get("unit") or ""),
    ], separators=(",", ":"))


def _history_bucket_coordinates(sample_time: float, now: float) -> tuple[int, int]:
    age = max(0.0, now - sample_time)
    duration = STATS_HISTORY_TIERS[-1][1]
    for max_age_seconds, bucket_seconds in STATS_HISTORY_TIERS:
        if age <= max_age_seconds:
            duration = bucket_seconds
            break
    return int(math.floor(sample_time / duration) * duration), duration


def _scan_agent_token_rows_to_spool(
    rows: list[dict[str, Any]],
    sample_time: float,
    previous_state: dict[str, dict[str, Any]],
    atom_state: dict[str, float],
    spool_path: Path,
    *,
    include_atoms: bool,
    measurements_override: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, float], int]:
    """Scan counters and stream stable usage atoms into a bounded SQLite spool."""

    measurements: list[dict[str, Any]] = list(measurements_override or [])
    measured_keys = {str(item.get("key") or "") for item in measurements}
    target_atom_state = dict(atom_state)
    connection = sqlite3.connect(spool_path)
    try:
        connection.execute("PRAGMA journal_mode=OFF")
        connection.execute("PRAGMA synchronous=OFF")
        connection.execute(
            "CREATE TABLE atoms (bucket_start INTEGER NOT NULL, duration INTEGER NOT NULL, "
            "event_key TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(bucket_start, duration, event_key))"
        )
        for row in rows:
            if not isinstance(row, dict):
                continue
            transcript = str(row.get("transcript") or "").strip()
            kind = str(row.get("kind") or "").strip().lower()
            key = str(row.get("key") or "").strip()
            if not transcript or not key:
                continue
            transcript_path = Path(transcript)
            if measurements_override is None:
                generated_tokens = session_files.transcript_generated_tokens(transcript_path, kind)
                if generated_tokens is None:
                    continue
                measurements.append({
                    "key": key,
                    "label": str(row.get("label") or key),
                    "tokens": generated_tokens,
                    "source": "transcript",
                    "identity": session_files.transcript_usage_identity(transcript_path, kind),
                    "models": session_files.transcript_generated_tokens_by_model(transcript_path, kind),
                })
                measured_keys.add(key)
            elif key not in measured_keys:
                continue
            if not include_atoms:
                continue
            previous_time = max(0.0, float(atom_state.get(key) or 0.0))
            tmux_fields = _tmux_usage_fields_from_row(row, key=key, label=str(row.get("label") or key), kind=kind)
            for atom in session_files.iter_transcript_usage_atoms(transcript_path, kind):
                if not (previous_time < atom.timestamp <= sample_time):
                    continue
                normalized = normalized_usage_atom(atom)
                if normalized is None:
                    continue
                normalized.update(tmux_fields)
                bucket_start, duration = _history_bucket_coordinates(atom.timestamp, sample_time)
                connection.execute(
                    "INSERT OR IGNORE INTO atoms(bucket_start, duration, event_key, payload) VALUES(?, ?, ?, ?)",
                    (bucket_start, duration, _agent_token_spool_identity(normalized), json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))),
                )
            target_atom_state[key] = sample_time
        connection.commit()
        count = int(connection.execute("SELECT COUNT(*) FROM atoms").fetchone()[0])
    finally:
        connection.close()
    return measurements, target_atom_state, count


def _run_agent_token_scan_process(
    rows: list[dict[str, Any]],
    sample_time: float,
    previous_state: dict[str, dict[str, Any]],
    seen_keys: set[str],
    scan_id: str,
    result_path_text: str,
    atom_state: dict[str, float] | None = None,
    include_atoms: bool = True,
) -> None:
    """Filesystem-only child entry point; it never opens the stats SQLite DB."""

    result_path = Path(result_path_text)
    partial_path = result_path.with_suffix(".partial.json")
    spool_path = result_path.with_suffix(".atoms.sqlite3")
    temporary_spool = spool_path.with_suffix(spool_path.suffix + ".tmp")
    temporary = result_path.with_suffix(result_path.suffix + ".tmp")

    def publish(payload: dict[str, Any], destination: Path = result_path) -> None:
        destination_temporary = destination.with_suffix(destination.suffix + ".tmp")
        destination_temporary.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        os.replace(destination_temporary, destination)

    try:
        temporary_spool.unlink(missing_ok=True)
        measurements, _unused_atom_state, _unused_atom_count = _scan_agent_token_rows_to_spool(
            rows, sample_time, previous_state, atom_state or {}, temporary_spool, include_atoms=False,
        )
        publish({
            "partial": True,
            "measurements": measurements,
            "seen_keys": sorted(seen_keys),
            "sample_time": sample_time,
            "previous_state": previous_state,
            "scan_id": scan_id,
        }, partial_path)
        temporary_spool.unlink(missing_ok=True)
        measurements, target_atom_state, atom_count = _scan_agent_token_rows_to_spool(
            rows, sample_time, previous_state, atom_state or {}, temporary_spool,
            include_atoms=include_atoms, measurements_override=measurements,
        )
        if include_atoms:
            os.replace(temporary_spool, spool_path)
        else:
            temporary_spool.unlink(missing_ok=True)
        result: dict[str, Any] = {
            "atoms_only": True,
            "measurements": [],
            "atom_spool": str(spool_path) if include_atoms else "",
            "atom_count": atom_count,
            "atom_state": target_atom_state,
            "seen_keys": sorted(seen_keys),
            "sample_time": sample_time,
            "previous_state": previous_state,
            "scan_id": scan_id,
        }
    except BaseException as exc:  # always leave a terminal result for the owner
        result = {"error": str(exc), "scan_id": scan_id}
    try:
        publish(result)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            temporary_spool.unlink(missing_ok=True)
        except OSError:
            pass


def stats_history_client_id(value: Any = "") -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return "".join(character if character.isalnum() or character in "_.:-" else "-" for character in raw)[:STATS_HISTORY_CLIENT_ID_MAX_LENGTH]


def _usage_atom_text(value: Any, *, default: str = "", limit: int = 256) -> str:
    return str(value if value is not None else default).strip()[:limit] or default


def _usage_atom_number(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) and number >= 0 else 0.0


def _usage_atom_timestamp_text(value: Any) -> str:
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        timestamp = 0.0
    if not math.isfinite(timestamp) or timestamp <= 0:
        return "9999-12-31T23:59:59Z"
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _rate_micro_usd(quantity: Any, rate: Any) -> int:
    try:
        value = (Decimal(str(_usage_atom_number(quantity))) * rate.usd * Decimal(1_000_000) / Decimal(int(rate.scale))).to_integral_value(rounding=ROUND_HALF_UP)
    except Exception:
        return 0
    return max(0, int(value))


def _component_lower_micro_usd(component: dict[str, Any]) -> int:
    if "lower_micro_usd" in component:
        return max(0, int(component.get("lower_micro_usd") or 0))
    if "estimated_lower_micro_usd" in component:
        return max(0, int(component.get("estimated_lower_micro_usd") or 0))
    return max(0, int(component.get("micro_usd") or 0)) if component.get("priced") else 0


def _component_upper_micro_usd(component: dict[str, Any]) -> int:
    if "upper_micro_usd" in component:
        return max(0, int(component.get("upper_micro_usd") or 0))
    if "estimated_upper_micro_usd" in component:
        return max(0, int(component.get("estimated_upper_micro_usd") or 0))
    return max(0, int(component.get("micro_usd") or 0)) if component.get("priced") else _component_lower_micro_usd(component)


def normalized_usage_atom(value: Any) -> dict[str, Any] | None:
    """Coerce a parser atom/dict into the bounded statsd storage contract."""

    fields = value.__dict__ if hasattr(value, "__dict__") else value
    if not isinstance(fields, dict):
        return None
    quantity = _usage_atom_number(fields.get("quantity"))
    if quantity <= 0:
        return None
    provider = _usage_atom_text(fields.get("provider"), default="unknown")
    model = _usage_atom_text(fields.get("model"), default="unknown")
    direction = _usage_atom_text(fields.get("direction"), default="unknown", limit=32)
    modality = _usage_atom_text(fields.get("modality"), default="unknown", limit=32)
    cache_role = _usage_atom_text(fields.get("cache_role"), default="none", limit=32)
    unit = _usage_atom_text(fields.get("unit"), default="tokens", limit=32)
    timestamp = _usage_atom_number(fields.get("timestamp"))
    event_id = _usage_atom_text(fields.get("event_id"), default="", limit=512)
    if not event_id:
        # Deliberately hash only structured dimensions, never raw prompt or
        # response bodies.  It supports idempotent replay/backfill when a
        # provider did not supply an event identifier.
        event_id = json.dumps([provider, model, timestamp, direction, modality, cache_role, unit, quantity, _usage_atom_text(fields.get("source"), limit=512)], separators=(",", ":"))
    source = _usage_atom_text(fields.get("source"), limit=512)
    transcript = _usage_atom_text(fields.get("transcript"), limit=4096)
    if not transcript and source.startswith("/") and Path(source).suffix.lower() in {".jsonl", ".ndjson"}:
        # Parser atoms already identify the canonical transcript file that
        # produced them. Keep it as attribution metadata, outside the event
        # identity used for idempotent replay.
        transcript = source
    return {
        "event_id": event_id,
        "timestamp": timestamp,
        "provider": provider,
        "model": model,
        "model_evidence": _usage_atom_text(fields.get("model_evidence"), default="unknown"),
        "effort": _usage_atom_text(fields.get("effort"), default="unknown", limit=64),
        "direction": direction,
        "modality": modality,
        "cache_role": cache_role,
        "unit": unit,
        "quantity": quantity,
        "root_thread_id": _usage_atom_text(fields.get("root_thread_id"), limit=256),
        "agent_thread_id": _usage_atom_text(fields.get("agent_thread_id"), limit=256),
        "parent_thread_id": _usage_atom_text(fields.get("parent_thread_id"), limit=256),
        "depth": max(0, int(_usage_atom_number(fields.get("depth")))),
        "endpoint": _usage_atom_text(fields.get("endpoint"), limit=128),
        "tool_name": _usage_atom_text(fields.get("tool_name"), limit=128),
        "call_id": _usage_atom_text(fields.get("call_id"), limit=256),
        "tmux_key": _usage_atom_text(fields.get("tmux_key"), limit=256),
        "tmux_label": _usage_atom_text(fields.get("tmux_label"), limit=256),
        "tmux_session": _usage_atom_text(fields.get("tmux_session"), limit=128),
        "tmux_window": _usage_atom_text(fields.get("tmux_window"), limit=64),
        "tmux_window_label": _usage_atom_text(fields.get("tmux_window_label"), limit=128),
        "agent_kind": _usage_atom_text(fields.get("agent_kind"), limit=32),
        "pricing_profile": _usage_atom_text(fields.get("pricing_profile"), default="default", limit=64),
        "service_tier": _usage_atom_text(fields.get("service_tier"), default="default", limit=64),
        # Set only by the durable retained-history migrator.  Live collection
        # never carries this marker, so a source replay can replace its own
        # prior staged atoms without deleting concurrent live usage.
        "backfill_source": _usage_atom_text(fields.get("backfill_source"), limit=512),
        "telemetry_complete": bool(fields.get("telemetry_complete")),
        "source": source,
        "transcript": transcript,
    }


def projected_usage_component(atom: Any, catalog: Any = None) -> dict[str, Any] | None:
    """Project one normalized atom to integer micro-USD via the catalog API."""

    normalized = normalized_usage_atom(atom)
    if normalized is None:
        return None

    def estimated_rate_fields() -> dict[str, Any]:
        estimate_rate_band = getattr(catalog, "estimate_rate_band", None)
        if catalog is None or not callable(estimate_rate_band):
            return {}
        band = estimate_rate_band(
            provider=normalized["provider"],
            direction=normalized["direction"], modality=normalized["modality"], cache_role=normalized["cache_role"],
            unit=normalized["unit"], profile=normalized["pricing_profile"], service_tier=normalized["service_tier"], timestamp=_usage_atom_timestamp_text(normalized["timestamp"]),
        )
        if band is None:
            return {}
        minimum = getattr(band, "minimum", None)
        maximum = getattr(band, "maximum", None)
        if minimum is None or maximum is None:
            return {}
        upper = _rate_micro_usd(normalized["quantity"], maximum)
        return {
            "lower_micro_usd": 0,
            "upper_micro_usd": upper,
            "estimated": True,
            "estimated_lower_micro_usd": 0,
            "estimated_upper_micro_usd": upper,
            "estimate_rate_min_usd": str(getattr(minimum, "usd", "")),
            "estimate_rate_max_usd": str(getattr(maximum, "usd", "")),
            "estimate_rate_scale": int(getattr(maximum, "scale", 0) or 0),
            "estimate_source_url": str(getattr(maximum, "source_url", "")),
            "estimate_catalog_revision": int(getattr(maximum, "catalog_revision", 0) or 0),
        }

    result = {
        **normalized,
        "micro_usd": 0,
        "lower_micro_usd": 0,
        "upper_micro_usd": 0,
        "priced": False,
        "estimated": False,
        "catalog_revision": 0,
        "source_url": "",
        "effective_from": "",
        "rate_usd": "",
        "rate_scale": 0,
        "estimated_lower_micro_usd": 0,
        "estimated_upper_micro_usd": 0,
        "estimate_rate_min_usd": "",
        "estimate_rate_max_usd": "",
        "estimate_rate_scale": 0,
        "estimate_source_url": "",
        "estimate_catalog_revision": 0,
    }
    if catalog is None or normalized["provider"] == "unknown" or normalized["model"] == "unknown":
        return {**result, **estimated_rate_fields()}
    rate = catalog.resolve_rate(
        provider=normalized["provider"], model=normalized["model"], direction=normalized["direction"], modality=normalized["modality"],
        cache_role=normalized["cache_role"], unit=normalized["unit"], profile=normalized["pricing_profile"], service_tier=normalized["service_tier"], timestamp=_usage_atom_timestamp_text(normalized["timestamp"]),
    )
    if rate is None:
        return {**result, **estimated_rate_fields()}
    micro_usd = _rate_micro_usd(normalized["quantity"], rate)
    return {
        **result,
        "micro_usd": micro_usd,
        "lower_micro_usd": micro_usd,
        "upper_micro_usd": micro_usd,
        "priced": True,
        "catalog_revision": int(rate.catalog_revision),
        "source_url": str(rate.source_url),
        "effective_from": str(getattr(rate, "effective_from", "")),
        "rate_usd": str(rate.usd),
        "rate_scale": int(rate.scale),
        "estimated_lower_micro_usd": micro_usd,
        "estimated_upper_micro_usd": micro_usd,
    }


def cost_summary_response(value: Any) -> dict[str, Any]:
    """Build the one public, range-summable cost-summary schema from atoms."""

    raw = value if isinstance(value, dict) else {}
    atoms = [atom for atom in raw.get("components", []) if isinstance(atom, dict)]

    def billed_class(atom: dict[str, Any]) -> str:
        # Model/source subtotals distinguish ordinary text token classes from
        # image/audio/request units.  This is presentation-only; the retained
        # atom remains the authoritative billable dimension tuple.
        if str(atom.get("unit") or "tokens").lower() != "tokens" or str(atom.get("modality") or "text").lower() != "text":
            return "other"
        cache_role = str(atom.get("cache_role") or "none").lower()
        if cache_role in {"read", "write_5m", "write_1h"}:
            return "cache"
        direction = str(atom.get("direction") or "unknown").lower()
        if direction == "input":
            return "input"
        if direction == "output":
            return "output"
        return "other"

    def aggregate(keys: tuple[str, ...], *, metadata: tuple[str, ...] = ()) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, ...], dict[str, Any]] = {}
        for atom in atoms:
            identity = tuple(str(atom.get(key) or "") for key in keys)
            row = grouped.setdefault(identity, {key: identity[index] for index, key in enumerate(keys)} | {
                "quantity": 0.0, "micro_usd": 0, "count": 0, "unpriced_count": 0, "unpriced_token_quantity": 0.0,
                "lower_micro_usd": 0, "upper_micro_usd": 0,
                "input_micro_usd": 0, "cache_micro_usd": 0, "output_micro_usd": 0, "other_micro_usd": 0,
                "input_lower_micro_usd": 0, "cache_lower_micro_usd": 0, "output_lower_micro_usd": 0, "other_lower_micro_usd": 0,
                "input_upper_micro_usd": 0, "cache_upper_micro_usd": 0, "output_upper_micro_usd": 0, "other_upper_micro_usd": 0,
                "token_quantity": 0.0, "input_tokens": 0.0, "cache_tokens": 0.0, "output_tokens": 0.0, "other_tokens": 0.0,
            })
            for field in metadata:
                metadata_value = str(atom.get(field) or "").strip()
                if metadata_value and not row.get(field):
                    row[field] = metadata_value
            quantity = _usage_atom_number(atom.get("quantity"))
            item_class = billed_class(atom)
            micro_usd = int(atom.get("micro_usd") or 0)
            lower_micro_usd = _component_lower_micro_usd(atom)
            upper_micro_usd = max(lower_micro_usd, _component_upper_micro_usd(atom))
            row["quantity"] += quantity
            row["micro_usd"] += micro_usd
            row["lower_micro_usd"] += lower_micro_usd
            row["upper_micro_usd"] += upper_micro_usd
            row["count"] += 1
            row["unpriced_count"] += 0 if atom.get("priced") else 1
            row[f"{item_class}_micro_usd"] += micro_usd
            row[f"{item_class}_lower_micro_usd"] += lower_micro_usd
            row[f"{item_class}_upper_micro_usd"] += upper_micro_usd
            if str(atom.get("unit") or "tokens").lower() == "tokens":
                row["token_quantity"] += quantity
                row[f"{item_class}_tokens"] += quantity
                if not atom.get("priced"):
                    row["unpriced_token_quantity"] += quantity
        return sorted(grouped.values(), key=lambda row: (-int(row["micro_usd"]), tuple(str(row[key]) for key in keys)))

    # A price revision is part of an atomic billable class.  Grouping events
    # across an effective-date boundary would make the displayed rate and its
    # source link dishonest even if the subtotal happened to remain correct.
    components = aggregate(("provider", "model", "effort", "pricing_profile", "service_tier", "direction", "modality", "cache_role", "unit", "catalog_revision", "source_url", "effective_from", "rate_usd", "rate_scale"))
    models = aggregate(("provider", "model", "effort"))
    sources = aggregate(("tmux_key", "tmux_label", "tmux_session", "tmux_window", "tmux_window_label", "agent_kind", "root_thread_id", "agent_thread_id", "parent_thread_id", "endpoint", "tool_name", "source"), metadata=("transcript",))
    tmux_windows = [row for row in aggregate(("tmux_key", "tmux_label", "tmux_session", "tmux_window", "tmux_window_label", "agent_kind")) if row.get("tmux_key") or row.get("tmux_session") or row.get("tmux_window")]
    known_micro_usd = sum(int(atom.get("micro_usd") or 0) for atom in atoms if atom.get("priced"))
    lower_micro_usd = sum(_component_lower_micro_usd(atom) for atom in atoms)
    upper_micro_usd = sum(max(_component_lower_micro_usd(atom), _component_upper_micro_usd(atom)) for atom in atoms)
    priced_count = sum(1 for atom in atoms if atom.get("priced"))
    unpriced_count = sum(1 for atom in atoms if not atom.get("priced"))
    unpriced_token_quantity = sum(
        _usage_atom_number(atom.get("quantity"))
        for atom in atoms
        if not atom.get("priced") and str(atom.get("unit") or "tokens").lower() == "tokens"
    )
    complete = not bool(raw.get("truncated")) and not bool(raw.get("lower_bound")) and unpriced_count == 0
    revisions = sorted({int(atom.get("catalog_revision") or 0) for atom in atoms if int(atom.get("catalog_revision") or 0) > 0})
    return {
        # `total_micro_usd` intentionally equals the known sum when telemetry
        # is incomplete; callers use `complete` to render `est. ≥$…`.
        "total_micro_usd": known_micro_usd,
        "known_micro_usd": known_micro_usd,
        "lower_micro_usd": lower_micro_usd,
        "upper_micro_usd": max(lower_micro_usd, upper_micro_usd),
        "priced_count": priced_count,
        "complete": complete,
        "unpriced_count": unpriced_count,
        "unpriced_token_quantity": unpriced_token_quantity,
        "components": components,
        "models": models,
        "sources": sources,
        "tmux_windows": tmux_windows,
        "catalog_revision": max(revisions, default=0),
        "active_catalog_revision": max(0, int(raw.get("active_catalog_revision") or 0)),
        "freshness": _usage_atom_text(raw.get("freshness"), default="unknown", limit=80),
    }


def _tmux_usage_fields_from_row(row: dict[str, Any], *, key: str = "", label: str = "", kind: str = "") -> dict[str, str]:
    """Extract Agent-token tmux identity for cost attribution.

    ``key`` is the existing Agent-token identity produced by the web process
    (`session|window|kind`).  The explicit row fields win, but parsing the key
    keeps older/direct statsd callers aligned with the same identity instead of
    inventing a second resolver.
    """

    clean_key = str(key or row.get("key") or "").strip()
    clean_label = str(label or row.get("label") or clean_key).strip()
    parts = clean_key.split("|")
    session = str(row.get("session") or (parts[0] if len(parts) > 0 else "")).strip()
    window = str(row.get("window") or (parts[1] if len(parts) > 1 else "")).strip()
    agent_kind = str(kind or row.get("kind") or (parts[2] if len(parts) > 2 else "")).strip().lower()
    return {
        "tmux_key": clean_key,
        "tmux_label": clean_label,
        "tmux_session": session,
        "tmux_window": window,
        "tmux_window_label": str(row.get("window_label") or clean_label or window).strip(),
        "agent_kind": agent_kind,
    }


def default_socket_path() -> Path:
    return safe_socket_path(STATE_DIR / "services" / STATSD_SOCKET_NAME, prefix="yolomux-statsd")


def default_reader_socket_path() -> Path:
    return safe_socket_path(STATE_DIR / "services" / STATS_READER_SOCKET_NAME, prefix="yolomux-stats-reader")


def default_database_path() -> Path:
    return STATE_DIR / STATSD_DATABASE_NAME


def default_sampler_owner_path(state_dir: Path = STATE_DIR) -> Path:
    return state_dir / "background-owner" / "owner.json"


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

    def __init__(
        self,
        socket_path: Path,
        database_path: Path,
        idle_seconds: float = STATSD_DEFAULT_IDLE_SECONDS,
        sampler_owner_path: Path | None = None,
        pricing_catalog: Any = None,
    ):
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
        self.last_history_profile: dict[str, Any] = {}
        self.history_request_count = 0
        self.history_cache_hit_count = 0
        self.sampler_owner_path = Path(sampler_owner_path) if sampler_owner_path is not None else None
        self.agent_token_consumer_until = 0.0
        self.last_sampler_success_at = 0.0
        self.last_sampler_failure = ""
        self.last_sampler_attempt_at = 0.0
        self.sampler_failure_count = 0
        self.sampler_missed_cycles = 0
        self.sampler_late_cycles = 0
        self.sampler_last_cycle_seconds = 0.0
        self.sampler_families: dict[str, dict[str, Any]] = {}
        self.agent_token_scan_lock = threading.Lock()
        # JSONL parsing can be CPU-bound for minutes. It must not share the
        # daemon GIL with the sampler/control listener; the process returns a
        # compact, local result file for this SQLite owner to persist.
        self.agent_token_scan_worker: multiprocessing.Process | None = None
        self.agent_token_scan_result_path: Path | None = None
        self.agent_token_scan_includes_atoms = False
        self.agent_token_atom_worker: multiprocessing.Process | None = None
        self.agent_token_atom_result_path: Path | None = None
        self.agent_token_scan_result: dict[str, Any] | None = None
        self.agent_token_scan_sequence = 0
        self.agent_token_scan_id = ""
        self.agent_token_scan_completion: dict[str, Any] | None = None
        self.agent_token_scan_persistence: dict[str, Any] | None = None
        # Load durable spool state only after the listener thread owns SQLite;
        # constructing a service must not eagerly bind the lazy connection to
        # the caller thread used by lifecycle tests and embedders.
        self.agent_token_atom_persistence: dict[str, Any] | None = None
        # Catalog ownership stays outside statsd.  Tests and the application
        # may inject its public resolve_rate surface; absent catalog means
        # usage remains visible but explicitly unpriced/lower-bound.
        self.pricing_catalog = pricing_catalog
        # Repricing remains on the listener/RPC thread, which is the single
        # SQLite owner. This state makes it cooperative instead of blocking
        # startup until the full retained history has been rewritten.
        self.pricing_reprojection: dict[str, Any] | None = None
        self.rollup_backfill: dict[str, int] | None = None
        # Dirty raw writes coalesce by durable rollup window.  A recovery page
        # may touch dozens of records in the same four windows; it must not
        # rebuild those windows inline on the RPC thread.
        self.rollup_pending: set[tuple[int, int]] = set()
        self.rollup_jobs: dict[tuple[int, int], dict[str, Any]] = {}
        self.retention_compaction: dict[str, Any] | None = None
        self.retention_normalization_complete = False
        self.maintenance_turn = 0

    def _pricing_catalog_metadata(self) -> tuple[str, int]:
        """Read local catalog status for a projection without fetching providers."""

        status = getattr(self.pricing_catalog, "status", None)
        if not callable(status):
            return "unknown", 0
        try:
            payload = status()
        except (OSError, sqlite3.Error, TypeError, ValueError):
            return "unknown", 0
        if not isinstance(payload, dict):
            return "unknown", 0
        state = _usage_atom_text(payload.get("state"), default="unknown", limit=80)
        try:
            revision = max(0, int(payload.get("catalog_revision") or 0))
        except (TypeError, ValueError):
            revision = 0
        return state, revision

    def _apply_pricing_catalog_metadata(self, summary: dict[str, Any]) -> None:
        freshness, revision = self._pricing_catalog_metadata()
        summary["freshness"] = freshness
        summary["active_catalog_revision"] = revision

    def _idle_shutdown_ready(self) -> bool:
        # The listener and SQLite writer are intentionally one thread. Advance
        # exactly one maintenance family after an actual 100ms accept timeout;
        # doing token + pricing + rollup work in one turn starves queued CPU RPCs.
        maintenance_kind = self.maintenance_turn % 4
        self.maintenance_turn += 1
        if maintenance_kind == 0:
            self._drain_agent_token_scan_result()
        elif maintenance_kind == 1:
            self._maybe_reproject_cost_summaries()
        elif maintenance_kind == 2:
            self._rollup_maintenance_step()
        else:
            self._retention_maintenance_step()
        with self.agent_token_scan_lock:
            scan_active = (
                self.agent_token_scan_worker is not None
                or self.agent_token_scan_result is not None
                or self.agent_token_scan_persistence is not None
                or self.agent_token_atom_worker is not None
                or self.agent_token_atom_persistence is not None
            )
        # An elected sampler is active work even while no browser reads YO!stats.
        maintenance_pending = self.pricing_reprojection is not None
        rollup_pending = bool(self.rollup_pending or self.rollup_jobs or self.rollup_backfill is not None)
        return not scan_active and not maintenance_pending and not rollup_pending and self.retention_compaction is None and not self.leases and monotonic_clock() - self.last_client_at >= self.idle_seconds

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
            model_rates = item.get("model_rates")
            if isinstance(model_rates, dict) and duration > 0:
                for model_rate in model_rates.values():
                    if not isinstance(model_rate, dict):
                        continue
                    model_seconds = max(0.0, float(model_rate.get("seconds") or 0.0))
                    if model_seconds > duration:
                        scale = duration / model_seconds
                        for field in ("total", "tokens", "seconds"):
                            model_rate[field] = max(0.0, float(model_rate.get(field) or 0.0) * scale)
                    model_rate["samples"] = 1.0 if float(model_rate.get("tokens") or model_rate.get("total") or 0.0) > 0 else 0.0
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

    def merge_records(
        self, records: list[dict[str, Any]], *, client_id: str = "", now: float | None = None,
        clear: bool = False, compact: bool = True,
    ) -> dict[str, Any]:
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
            try:
                requested_duration = int(record.get("_statsd_duration") or 0)
            except (TypeError, ValueError):
                requested_duration = 0
            valid_durations = {bucket_seconds for _max_age, bucket_seconds in STATS_HISTORY_TIERS}
            duration = requested_duration if requested_duration in valid_durations else self._bucket_seconds(sample_time, sample_now)
            start = int(math.floor(sample_time / duration) * duration)
            bucket = self.store.bucket(start, duration) or stats_store.empty_bucket(start, duration)
            clients = bucket.setdefault("clients", {})
            client = clients.get(clean_client_id)
            if not isinstance(client, dict):
                client = stats_store.empty_client_bucket()
                clients[clean_client_id] = client
            # Old or unusually busy buckets may predate the byte-aware cost
            # bound. Compact that projection before adding any token/process
            # field so an otherwise small live update cannot overflow SQLite's
            # bounded bucket JSON.
            record_changed = self._merge_usage_components(bucket, [])
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
            self._enqueue_persisted_rollups(sample_time)
            changed += 1
        if changed:
            if compact:
                self._compact_history(sample_now)
            else:
                self._enqueue_retention_compaction(sample_now)
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

    def merge_server_records(
        self,
        records: list[dict[str, Any]],
        *,
        now: float | None = None,
        compact: bool = True,
        refresh_rollups: bool = True,
    ) -> dict[str, Any]:
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
            coverage = record.get("_stats_coverage") if isinstance(record.get("_stats_coverage"), dict) else {}
            coverage_family = str(coverage.get("family") or "").strip()
            coverage_epoch = str(coverage.get("epoch_id") or "").strip()
            coverage_cadence = self._positive_finite(coverage.get("cadence_seconds"))
            try:
                coverage_generation = max(0, int(coverage.get("owner_generation") or 0))
            except (TypeError, ValueError):
                coverage_generation = 0
            coverage_valid = bool(
                coverage_family in stats_store.STATS_COVERAGE_FAMILIES
                and coverage_epoch
                and coverage_cadence
            )
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
                    target = target_rates.setdefault(key, {"label": str(raw_rate.get("label") or key), "total": 0.0, "samples": 0.0, "tokens": 0.0, "seconds": 0.0, "source": "", "model_rates": {}})
                    for field in ("total", "samples", "tokens", "seconds"):
                        target[field] = float(target.get(field) or 0.0) + self._positive_finite(raw_rate.get(field))
                    target["label"] = str(raw_rate.get("label") or target.get("label") or key)
                    if raw_rate.get("source"):
                        target["source"] = str(raw_rate["source"])
                    self._merge_agent_token_model_rates(target, raw_rate.get("model_rates"))
                    record_changed = True
            record_changed = self._merge_usage_components(bucket, record.get("usage_atoms")) or record_changed
            record_changed = self._merge_host_metrics(bucket, record.get("host_metrics")) or record_changed
            # A successful zero-valued sample is still coverage.  Persist an
            # empty bucket marker so charts can distinguish measured zero from
            # a sampler outage, without inflating any aggregate count.
            record_changed = record_changed or coverage_valid
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
            coverage_samples: list[dict[str, Any]] = []
            if coverage_valid:
                if coverage_family == "agent_tokens":
                    coverage_families = ("agent_tokens", "cost")
                elif coverage_family == "cpu":
                    coverage_families = ("cpu", "raw")
                else:
                    coverage_families = (coverage_family,)
                for family in coverage_families:
                    coverage_start = max(0, int(math.floor(sample_time)))
                    coverage_cadence_seconds = max(1, int(math.ceil(coverage_cadence)))
                    coverage_samples.append({
                        "family": family,
                        "start": coverage_start,
                        "end": coverage_start + coverage_cadence_seconds,
                        "cadence": coverage_cadence_seconds,
                        "epoch_id": coverage_epoch,
                        "owner_generation": coverage_generation,
                    })
            self.store.upsert_bucket_with_coverage(bucket, coverage_samples)
            if refresh_rollups:
                self._refresh_persisted_rollups(sample_time)
            else:
                self._enqueue_persisted_rollups(sample_time)
            changed += 1
        if changed and compact:
            self._compact_history(sample_now)
        elif changed:
            self._enqueue_retention_compaction(sample_now)
        if changed:
            self._encoded_query_cache.clear()
        return {"ok": True, "changed": changed, "sequence": int(self.store.diagnostics().get("sequence") or 0)}

    def _refresh_persisted_rollups(self, sample_time: float) -> None:
        """Rebuild only the aggregate windows touched by one durable write."""

        for duration in STATS_HISTORY_PERSISTED_ROLLUP_SECONDS:
            start = int(math.floor(sample_time / duration) * duration)
            self._refresh_persisted_rollup_window(start, duration)

    def _refresh_persisted_rollup_window(self, start: int, duration: int) -> None:
        sources = self.store.query_buckets(start=start, end=start + duration)
        if not sources:
            return
        aggregate = stats_store.empty_bucket(start, duration)
        for source in sources:
            self._merge_bucket(aggregate, source)
        self.store.upsert_rollup(aggregate)

    def _enqueue_persisted_rollups(self, sample_time: float) -> None:
        for duration in STATS_HISTORY_PERSISTED_ROLLUP_SECONDS:
            start = int(math.floor(sample_time / duration) * duration)
            key = (start, duration)
            # A partially accumulated job cannot include a new raw write
            # safely; discard it and recompute the coalesced window.
            self.rollup_jobs.pop(key, None)
            self.rollup_pending.add(key)

    def _rollup_maintenance_step(self) -> dict[str, Any]:
        """Cooperatively converge dirty and startup rollups between requests."""
        deadline = monotonic_clock() + STATSD_ROLLUP_MAINTENANCE_BUDGET_SECONDS
        progressed = 0
        if self.rollup_pending:
            start, duration = min(self.rollup_pending)
            self.rollup_pending.remove((start, duration))
            self.rollup_jobs[(start, duration)] = {
                "after_start": start - 1,
                "after_duration": -1,
                "aggregate": stats_store.empty_bucket(start, duration),
            }
            progressed = 1
        elif self.rollup_jobs and monotonic_clock() < deadline:
            start, duration = min(self.rollup_jobs)
            job = self.rollup_jobs[(start, duration)]
            rows = self.store.rollup_source_page(
                start=start, end=start + duration,
                after_start=int(job["after_start"]), after_duration=int(job["after_duration"]),
                limit=STATSD_ROLLUP_MAINTENANCE_PAGE_ROWS,
            )
            if rows:
                for source in rows:
                    self._merge_bucket(job["aggregate"], source)
                last = rows[-1]
                job["after_start"], job["after_duration"] = int(last["start"]), int(last["duration"])
                progressed = len(rows)
            else:
                aggregate = job["aggregate"]
                self.rollup_jobs.pop((start, duration), None)
                if int(aggregate.get("sequence") or 0):
                    self.store.upsert_rollup(aggregate)
                progressed = 1
        elif self.rollup_backfill is not None and monotonic_clock() < deadline:
            rows = self.store.maintenance_buckets_after(
                after_start=int(self.rollup_backfill["after_start"]),
                after_duration=int(self.rollup_backfill["after_duration"]), limit=1,
            )
            if not rows:
                self.rollup_backfill = None
            else:
                source = rows[0]
                self.rollup_backfill["after_start"] = int(source["start"])
                self.rollup_backfill["after_duration"] = int(source["duration"])
                self.rollup_backfill["processed"] = int(self.rollup_backfill["processed"]) + 1
                self._enqueue_persisted_rollups(float(source["start"]))
                progressed = 1
        pending = bool(self.rollup_pending or self.rollup_jobs or self.rollup_backfill is not None)
        return {"ok": True, "pending": pending, "processed": progressed}

    def _rollup_backfill_step(self) -> dict[str, Any]:
        """Compatibility name for focused callers; use the shared queue."""
        return self._rollup_maintenance_step()

    def _enqueue_retention_compaction(self, now: float) -> None:
        if self.retention_compaction is None:
            self.retention_compaction = {"now": float(now), "next_now": 0.0, "phase": "buckets"}
            return
        # Freeze one pass's tier boundaries. New writes are already assigned to
        # their current tier; remember a later epoch without restarting the
        # active pass on every one-second sample.
        self.retention_compaction["next_now"] = max(
            float(self.retention_compaction.get("next_now") or 0.0), float(now)
        )

    def _retention_maintenance_step(self) -> dict[str, Any]:
        """Compact or expire exactly one durable row between live RPCs."""

        state = self.retention_compaction
        if state is None:
            return {"ok": True, "pending": False, "processed": 0}
        now = float(state["now"])
        cutoff = now - STATS_HISTORY_RETENTION_SECONDS
        if state["phase"] == "buckets":
            source = self.store.retention_candidate(
                now=now, retention_seconds=STATS_HISTORY_RETENTION_SECONDS, tiers=STATS_HISTORY_TIERS,
            )
            if source is not None:
                start, duration = int(source["start"]), int(source["duration"])
                if start < cutoff:
                    self.store.replace_compacted_bucket(start, duration, None)
                else:
                    normalized = self._merge_usage_components(source, [])
                    target_duration = max(duration, self._bucket_seconds(float(start), now))
                    target_start = int(math.floor(start / target_duration) * target_duration)
                    if (target_start, target_duration) != (start, duration):
                        target = self.store.bucket(target_start, target_duration) or stats_store.empty_bucket(target_start, target_duration)
                        self._merge_bucket(target, source)
                        self.store.replace_compacted_bucket(start, duration, target)
                        # A rollup job may have observed the source before this
                        # atomic move. Invalidate it so its eventual projection
                        # is derived wholly from the final compacted sources.
                        self._enqueue_persisted_rollups(float(target_start))
                    elif normalized:
                        self.store.upsert_bucket(source)
                return {"ok": True, "pending": True, "processed": 1}
            if self.retention_normalization_complete:
                state["phase"] = "rollups"
            else:
                state.update({"phase": "normalize", "after_start": -1, "after_duration": -1})
        if state["phase"] == "normalize":
            rows = self.store.maintenance_buckets_after(
                after_start=int(state["after_start"]), after_duration=int(state["after_duration"]), limit=1,
            )
            if rows:
                source = rows[0]
                state["after_start"] = int(source["start"])
                state["after_duration"] = int(source["duration"])
                if self._merge_usage_components(source, []):
                    self.store.upsert_bucket(source)
                return {"ok": True, "pending": True, "processed": 1}
            self.retention_normalization_complete = True
            state["phase"] = "rollups"
        stale_rollup = self.store.oldest_rollup_before(cutoff)
        if stale_rollup is not None:
            self.store.delete_rollup(*stale_rollup)
            return {"ok": True, "pending": True, "processed": 1}
        # Epoch intervals are independent of raw-row compaction, but obey the
        # same retention window once the row pass has converged.
        self.store.retain_after(cutoff)
        next_now = float(state.get("next_now") or 0.0)
        self.retention_compaction = (
            {"now": next_now, "next_now": 0.0, "phase": "buckets"}
            if next_now > now else None
        )
        self._encoded_query_cache.clear()
        return {"ok": True, "pending": self.retention_compaction is not None, "processed": 0}

    def _merge_usage_components(self, bucket: dict[str, Any], atoms: Any) -> bool:
        """Persist a bounded, idempotent component projection in one bucket."""

        if not isinstance(atoms, list):
            return False
        summary = bucket.setdefault("cost_summary", {})
        existing = summary.get("components") if isinstance(summary.get("components"), list) else []
        components: list[dict[str, Any]] = []
        encoded_bytes = 2
        transcript_sources: set[tuple[str, str]] = set()
        changed = False

        def append_if_bounded(raw_component: dict[str, Any]) -> bool:
            nonlocal encoded_bytes, changed
            component = raw_component
            transcript = str(component.get("transcript") or "")
            transcript_key = (str(component.get("source") or ""), transcript)
            if transcript and transcript_key in transcript_sources:
                component = {**component, "transcript": ""}
                changed = True
            encoded = json.dumps(component, sort_keys=True, separators=(",", ":")).encode("utf-8")
            next_bytes = encoded_bytes + len(encoded) + (1 if components else 0)
            if len(components) >= STATS_COST_SUMMARY_MAX_COMPONENTS or next_bytes > STATS_COST_SUMMARY_MAX_BYTES:
                return False
            components.append(component)
            encoded_bytes = next_bytes
            if transcript:
                transcript_sources.add(transcript_key)
            return True

        for item in existing:
            if not isinstance(item, dict) or not append_if_bounded(item):
                changed = True
                summary["lower_bound"] = True
                summary["truncated"] = True
        seen = {
            (str(item.get("event_id") or ""), str(item.get("direction") or ""), str(item.get("modality") or ""), str(item.get("cache_role") or ""), str(item.get("unit") or ""))
            for item in components
        }
        for atom in atoms:
            component = projected_usage_component(atom, self.pricing_catalog)
            if component is None:
                continue
            identity = (component["event_id"], component["direction"], component["modality"], component["cache_role"], component["unit"])
            if identity in seen:
                continue
            if not append_if_bounded(component):
                # This cannot claim exactness after eviction because a future
                # replay cannot prove the evicted event was absent.
                summary["lower_bound"] = True
                summary["truncated"] = True
                changed = True
                continue
            seen.add(identity)
            changed = True
        if not changed:
            return False
        self._recalculate_usage_summary(summary, components)
        self._apply_pricing_catalog_metadata(summary)
        return True

    @staticmethod
    def _recalculate_usage_summary(summary: dict[str, Any], components: list[dict[str, Any]], *, legacy_output_only: bool = False) -> None:
        """Keep projection totals derived from retained raw usage dimensions.

        ``lower_bound`` is intentionally recomputed from durable facts rather
        than carried forward from an older catalog: a formerly-unpriced atom
        can become priceable after Refresh, whereas truncated and legacy
        output-only history can never become exact without missing telemetry.
        """
        summary["components"] = components
        known_micro_usd = sum(int(item.get("micro_usd") or 0) for item in components if item.get("priced"))
        lower_micro_usd = sum(_component_lower_micro_usd(item) for item in components)
        upper_micro_usd = sum(max(_component_lower_micro_usd(item), _component_upper_micro_usd(item)) for item in components)
        summary["total_micro_usd"] = known_micro_usd
        summary["known_micro_usd"] = known_micro_usd
        summary["lower_micro_usd"] = lower_micro_usd
        summary["upper_micro_usd"] = max(lower_micro_usd, upper_micro_usd)
        summary["priced_components"] = sum(1 for item in components if item.get("priced"))
        summary["unpriced_components"] = sum(1 for item in components if not item.get("priced"))
        summary["lower_bound"] = bool(summary.get("truncated")) or legacy_output_only or any(
            not item.get("priced") or not item.get("telemetry_complete") for item in components
        )
        summary["catalog_revisions"] = sorted({int(item.get("catalog_revision") or 0) for item in components if int(item.get("catalog_revision") or 0) > 0})

    def _pricing_catalog_revision(self) -> int | None:
        status = getattr(self.pricing_catalog, "status", None)
        if not callable(status):
            return None
        try:
            return max(0, int((status() or {}).get("catalog_revision") or 0))
        except (TypeError, ValueError, sqlite3.Error, OSError):
            return None

    def _schedule_cost_reprojection(self) -> dict[str, Any]:
        revision = self._pricing_catalog_revision()
        if revision is None:
            return {"ok": True, "reason": "catalog_has_no_status"}
        if revision <= 0:
            return {"ok": True, "reason": "catalog_status_unavailable"}
        marker = f"{revision}:policy-{STATSD_PRICING_PROJECTION_POLICY_VERSION}"
        if self.store.metadata_value(STATSD_PRICING_REPROJECTION_MARKER) == marker:
            self.pricing_reprojection = None
            return {"ok": True, "changed": 0, "reason": "current_catalog"}
        if self.pricing_reprojection is None or int(self.pricing_reprojection["revision"]) != revision:
            self.pricing_reprojection = {"revision": revision, "marker": marker, "after_start": -1, "after_duration": -1, "changed": 0, "processed": 0}
        return {"ok": True, "pending": True, "revision": revision}

    def _reproject_cost_summaries_step(self) -> dict[str, Any]:
        """Reprice one bounded keyset page on statsd's SQLite owner thread."""
        pending = self.pricing_reprojection
        if pending is None:
            return {"ok": True, "changed": 0, "reason": "current_catalog"}
        if self._pricing_catalog_revision() != int(pending["revision"]):
            self.pricing_reprojection = None
            return self._schedule_cost_reprojection()
        buckets = self.store.maintenance_buckets_after(
            after_start=int(pending["after_start"]), after_duration=int(pending["after_duration"]),
            limit=STATSD_PRICING_REPROJECTION_BATCH_BUCKETS,
        )
        if not buckets:
            self.store.set_metadata_value(STATSD_PRICING_REPROJECTION_MARKER, str(pending["marker"]))
            self.pricing_reprojection = None
            if pending["changed"]:
                self._encoded_query_cache.clear()
            return {"ok": True, "changed": int(pending["changed"]), "processed": int(pending["processed"]), "reason": "complete"}
        changed = 0
        next_sequence = int(self.store.diagnostics().get("sequence") or 0)
        with self.store._connection() as connection:
            for bucket in buckets:
                summary = bucket.get("cost_summary") if isinstance(bucket.get("cost_summary"), dict) else {}
                components = summary.get("components") if isinstance(summary.get("components"), list) else []
                projected_components = [item for item in (projected_usage_component(component, self.pricing_catalog) for component in components) if item is not None]
                replacement = dict(summary)
                self._recalculate_usage_summary(replacement, projected_components, legacy_output_only=not projected_components and bool(bucket.get("tokens_per_agent_total") or bucket.get("agent_token_rates")))
                self._apply_pricing_catalog_metadata(replacement)
                if replacement != summary:
                    next_sequence += 1
                    bucket["cost_summary"] = replacement
                    bucket["server_sequence"] = max(int(bucket.get("server_sequence") or 0), next_sequence)
                    bucket["sequence"] = max(int(bucket.get("sequence") or 0), next_sequence)
                    self.store._upsert_bucket(connection, bucket)
                    changed += 1
        last = buckets[-1]
        pending["after_start"], pending["after_duration"] = int(last["start"]), int(last["duration"])
        pending["changed"], pending["processed"] = int(pending["changed"]) + changed, int(pending["processed"]) + len(buckets)
        if len(buckets) < STATSD_PRICING_REPROJECTION_BATCH_BUCKETS:
            self.store.set_metadata_value(STATSD_PRICING_REPROJECTION_MARKER, str(pending["marker"]))
            total_changed, processed = int(pending["changed"]), int(pending["processed"])
            self.pricing_reprojection = None
            if total_changed:
                self._encoded_query_cache.clear()
            return {"ok": True, "changed": total_changed, "processed": processed, "reason": "complete"}
        return {"ok": True, "changed": changed, "processed": int(pending["processed"]), "pending": True, "revision": pending["revision"]}

    def reproject_cost_summaries(self) -> dict[str, Any]:
        """Synchronously finish an explicit repricing request in bounded pages."""
        scheduled = self._schedule_cost_reprojection()
        if not scheduled.get("pending"):
            return scheduled
        result: dict[str, Any] = scheduled
        while self.pricing_reprojection is not None:
            result = self._reproject_cost_summaries_step()
        return {"ok": True, "changed": int(result.get("changed") or 0), "sequence": int(self.store.diagnostics().get("sequence") or 0)}

    def _maybe_reproject_cost_summaries(self) -> dict[str, Any]:
        """Advance at most one bounded repricing page per service turn."""
        scheduled = self._schedule_cost_reprojection()
        return self._reproject_cost_summaries_step() if scheduled.get("pending") else scheduled

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
            raw_models = raw_item.get("models")
            if isinstance(raw_models, dict):
                item["models"] = {
                    str(model or "unknown").strip()[:256] or "unknown": PersistentStatsService._positive_finite(total)
                    for model, total in raw_models.items()
                    if PersistentStatsService._positive_finite(total) > 0
                }
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

    def _agent_token_atom_state(self) -> dict[str, float]:
        raw = self.store.metadata_value(STATSD_AGENT_TOKEN_ATOM_STATE_KEY)
        try:
            decoded = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            decoded = {}
        state: dict[str, float] = {}
        if isinstance(decoded, dict):
            for raw_key, raw_time in decoded.items():
                try:
                    timestamp = max(0.0, float(raw_time or 0.0))
                except (TypeError, ValueError):
                    continue
                if raw_key and timestamp:
                    state[str(raw_key)] = timestamp
        if state:
            return state
        # Upgrade compatibility: before atom and counter watermarks were
        # separated, the counter state advanced only after every atom write.
        return {
            key: float(item.get("time") or 0.0)
            for key, item in self._agent_token_state().items()
            if float(item.get("time") or 0.0) > 0
        }

    def _set_agent_token_atom_state(self, state: dict[str, float]) -> None:
        self.store.set_metadata_value(
            STATSD_AGENT_TOKEN_ATOM_STATE_KEY,
            json.dumps(state, sort_keys=True, separators=(",", ":")),
        )

    def _load_agent_token_atom_spool(self) -> dict[str, Any] | None:
        raw = self.store.metadata_value(STATSD_AGENT_TOKEN_ATOM_SPOOL_KEY)
        try:
            payload = json.loads(raw) if raw else None
        except (TypeError, ValueError):
            payload = None
        path = Path(str(payload.get("path") or "")) if isinstance(payload, dict) else Path()
        # Once this process owns the statsd lock, no unreferenced scan spool
        # can still have a live producer. Remove crash leftovers so a failed
        # handoff cannot leak hundreds of MiB indefinitely.
        for candidate in self.socket_path.parent.glob("statsd-agent-token-scan-*.atoms.sqlite3*"):
            if candidate != path:
                try:
                    candidate.unlink(missing_ok=True)
                except OSError:
                    pass
        if not isinstance(payload, dict):
            return None
        try:
            valid_path = path.parent.resolve() == self.socket_path.parent.resolve() and path.is_file()
        except OSError:
            valid_path = False
        if not valid_path:
            # Keep the old atom watermark: the next scan safely rebuilds a
            # missing/corrupt spool instead of silently skipping its atoms.
            self.store.set_metadata_value(STATSD_AGENT_TOKEN_ATOM_SPOOL_KEY, "")
            return None
        payload["path"] = str(path)
        payload["cursor_start"] = int(payload.get("cursor_start", -1))
        payload["cursor_duration"] = int(payload.get("cursor_duration", -1))
        payload["cursor_event"] = str(payload.get("cursor_event") or "")
        payload["changed"] = int(payload.get("changed") or 0)
        return payload

    def _store_agent_token_atom_spool(self, persistence: dict[str, Any] | None) -> None:
        self.agent_token_atom_persistence = persistence
        self.store.set_metadata_value(
            STATSD_AGENT_TOKEN_ATOM_SPOOL_KEY,
            json.dumps(persistence, sort_keys=True, separators=(",", ":")) if persistence is not None else "",
        )

    def _install_agent_token_atom_spool(self, result: dict[str, Any]) -> None:
        path = Path(str(result.get("atom_spool") or ""))
        if not path.is_file():
            return
        if not self.store.metadata_value(STATSD_AGENT_TOKEN_ATOM_STATE_KEY):
            # Freeze the upgrade fallback before the independent counter state
            # advances. Otherwise a restart could mistake the new live
            # baseline for proof that the still-pending atoms were durable.
            self._set_agent_token_atom_state(self._agent_token_atom_state())
        persistence = {
            "path": str(path),
            "cursor_start": -1,
            "cursor_duration": -1,
            "cursor_event": "",
            "target_state": result.get("atom_state") if isinstance(result.get("atom_state"), dict) else {},
            "sample_time": float(result.get("sample_time") or time.time()),
            "count": max(0, int(result.get("atom_count") or 0)),
            "changed": 0,
        }
        # The durable pointer is committed before the independent live counter
        # baseline can advance. A crash therefore resumes atoms from the old
        # watermark; replay is safe because event identities are stable.
        self._store_agent_token_atom_spool(persistence)

    def _drain_agent_token_atom_spool(self) -> bool:
        persistence = self.agent_token_atom_persistence
        if persistence is None:
            return False
        path = Path(str(persistence.get("path") or ""))
        try:
            connection = sqlite3.connect(path)
            cursor_start = int(persistence.get("cursor_start", -1))
            cursor_duration = int(persistence.get("cursor_duration", -1))
            cursor_event = str(persistence.get("cursor_event") or "")
            rows = connection.execute(
                "SELECT bucket_start, duration, event_key, payload FROM atoms "
                "WHERE bucket_start > ? "
                "OR (bucket_start = ? AND duration > ?) "
                "OR (bucket_start = ? AND duration = ? AND event_key > ?) "
                "ORDER BY bucket_start, duration, event_key LIMIT ?",
                (
                    cursor_start, cursor_start, cursor_duration,
                    cursor_start, cursor_duration, cursor_event,
                    STATSD_AGENT_TOKEN_ATOM_PAGE_RECORDS,
                ),
            ).fetchall()
            if not rows:
                connection.close()
                target_state = persistence.get("target_state")
                if isinstance(target_state, dict):
                    self._set_agent_token_atom_state({
                        str(key): max(0.0, float(value or 0.0))
                        for key, value in target_state.items()
                    })
                self._store_agent_token_atom_spool(None)
                path.unlink(missing_ok=True)
                return True
            connection.close()
            records: list[dict[str, Any]] = []
            for raw_start, raw_duration, _event_key, raw_payload in rows:
                bucket_start, duration = int(raw_start), int(raw_duration)
                if not records or records[-1]["_statsd_duration"] != duration or records[-1]["time"] != bucket_start:
                    records.append({"time": bucket_start, "_statsd_duration": duration, "usage_atoms": []})
                records[-1]["usage_atoms"].append(json.loads(str(raw_payload)))
            merged = self.merge_server_records(
                records,
                now=float(persistence.get("sample_time") or time.time()),
                compact=False,
                refresh_rollups=False,
            )
        except (OSError, RuntimeError, TypeError, ValueError, sqlite3.Error) as exc:
            self.last_sampler_failure = redact_local_service_text(exc)
            return True
        persistence["cursor_start"] = int(rows[-1][0])
        persistence["cursor_duration"] = int(rows[-1][1])
        persistence["cursor_event"] = str(rows[-1][2])
        persistence["changed"] = int(persistence.get("changed") or 0) + int(merged.get("changed") or 0)
        self._store_agent_token_atom_spool(persistence)
        return True

    def _drain_agent_token_atom_worker(self) -> bool:
        worker = self.agent_token_atom_worker
        result_path = self.agent_token_atom_result_path
        if worker is None or result_path is None or (worker.is_alive() and not result_path.is_file()):
            return False
        if not worker.is_alive():
            worker.join(timeout=0)
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
            if result.get("error"):
                self.last_sampler_failure = redact_local_service_text(result["error"])
            else:
                self._install_agent_token_atom_spool(result)
        except (OSError, RuntimeError, TypeError, ValueError, sqlite3.Error) as exc:
            self.last_sampler_failure = redact_local_service_text(exc)
        finally:
            try:
                result_path.unlink(missing_ok=True)
            except OSError:
                pass
            self.agent_token_atom_worker = None
            self.agent_token_atom_result_path = None
        return True

    @staticmethod
    def _agent_token_delta_records(
        key: str,
        label: str,
        start_time: float,
        end_time: float,
        token_delta: float,
        model_deltas: dict[str, float] | None = None,
    ) -> list[dict[str, Any]]:
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
            raw_model_deltas = {
                str(model or "unknown").strip()[:256] or "unknown": PersistentStatsService._positive_finite(value)
                for model, value in (model_deltas or {}).items()
                if PersistentStatsService._positive_finite(value) > 0
            }
            attributed = sum(raw_model_deltas.values())
            if attributed > token_delta and attributed > 0:
                scale = token_delta / attributed
                raw_model_deltas = {model: value * scale for model, value in raw_model_deltas.items()}
                attributed = token_delta
            if token_delta > attributed:
                raw_model_deltas["unknown"] = raw_model_deltas.get("unknown", 0.0) + token_delta - attributed
            model_rates = {
                model: {
                    "total": value * (overlap / elapsed),
                    "samples": 1.0,
                    "tokens": value * (overlap / elapsed),
                    "seconds": overlap,
                }
                for model, value in raw_model_deltas.items()
            }
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
                    "model_rates": model_rates,
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
        *,
        persist_state: bool = True,
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
            models = {
                str(model or "unknown").strip()[:256] or "unknown": self._positive_finite(total)
                for model, total in (measurement.get("models") if isinstance(measurement.get("models"), dict) else {}).items()
                if self._positive_finite(total) > 0
            }
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
                token_delta = token_count - previous_tokens
                previous_models = previous.get("models") if isinstance(previous.get("models"), dict) else {}
                model_deltas = {
                    model: max(0.0, total - self._positive_finite(previous_models.get(model)))
                    for model, total in models.items()
                    if previous_models and total > self._positive_finite(previous_models.get(model))
                }
                records.extend(self._agent_token_delta_records(key, label, previous_time, sample_time, token_delta, model_deltas))
            state[key] = {
                "tokens": token_count,
                "time": sample_time,
                "label": label,
                "source": token_source,
                "identity": token_identity,
                "models": models,
            }
        for key in list(state):
            if key not in seen_keys:
                state.pop(key, None)
        if persist_state:
            self._set_agent_token_state(state)
        return {"ok": True, "records": records, "state": state}

    def _scan_agent_token_rows(
        self,
        rows: list[dict[str, Any]],
        sample_time: float,
        previous_state: dict[str, dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Compatibility helper for synchronous callers and direct tests."""

        return _scan_agent_token_rows_in_worker(rows, sample_time, previous_state)

    def _persist_agent_token_scan(
        self,
        measurements: list[dict[str, Any]],
        atom_records: list[dict[str, Any]],
        seen_keys: set[str],
        sample_time: float,
        previous_state: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        claimed = self.claim_agent_token_deltas(
            measurements,
            seen_keys,
            sample_time,
            fallback_state=previous_state,
            persist_state=False,
        )
        records = [*claimed.get("records", []), *atom_records]
        merged = self._merge_agent_token_scan_records(records, sample_time)
        # Commit the counter baseline only after every derived record is
        # durable. A failed merge must remain retryable on the next scan.
        self._set_agent_token_state(claimed["state"])
        # Claiming and persistence belong to the statsd writer. Returning the
        # transcript atom stream over the bounded metadata channel could exceed
        # 256 KiB after the baseline had already advanced, permanently losing
        # the corresponding graph delta. Keep the RPC response compact.
        claimed["records"] = []
        claimed["persisted_records"] = len(records)
        claimed["merge"] = merged
        return claimed

    def _merge_agent_token_scan_records(self, records: list[dict[str, Any]], sample_time: float) -> dict[str, Any]:
        """Merge recovery atoms in bounded RPC-sized batches before advancing state.

        A transcript recovery can legitimately emit more usage atoms than the
        public history request limit.  It still runs in the statsd owner, so
        split it here rather than relaxing the request bound shared by browser
        clients.  Exceptions deliberately propagate: the caller only commits
        the token counter baseline after every batch is durable.
        """

        if not records:
            return {"ok": True, "changed": 0, "sequence": int(self.store.diagnostics().get("sequence") or 0), "batches": 0}
        changed = 0
        sequence = int(self.store.diagnostics().get("sequence") or 0)
        batches = 0
        for start in range(0, len(records), STATS_HISTORY_POST_MAX_RECORDS):
            result = self.merge_server_records(
                records[start : start + STATS_HISTORY_POST_MAX_RECORDS],
                now=sample_time,
                compact=False,
            )
            changed += int(result.get("changed") or 0)
            sequence = int(result.get("sequence") or sequence)
            batches += 1
        if changed:
            # A recovery may span many request-sized batches.  Compact only
            # once after its final durable write so the single stats writer
            # remains responsive instead of repeatedly compacting the entire
            # history database for every 1,000 records.
            self._compact_history(sample_time)
            self._encoded_query_cache.clear()
        return {"ok": True, "changed": changed, "sequence": sequence, "batches": batches}

    def claim_agent_token_deltas_from_rows(
        self,
        rows: list[dict[str, Any]],
        seen_keys: set[str],
        sample_time: float,
        fallback_state: Any = None,
    ) -> dict[str, Any]:
        previous_state = self._agent_token_state(fallback_state)
        measurements, atom_records = self._scan_agent_token_rows(rows, sample_time, previous_state)
        return self._persist_agent_token_scan(measurements, atom_records, seen_keys, sample_time, previous_state)

    def _drain_agent_token_scan_result(self) -> bool:
        """Advance a completed scan in bounded, retryable SQLite-owner pages."""

        self._drain_agent_token_atom_worker()
        with self.agent_token_scan_lock:
            result = self.agent_token_scan_result
            worker = self.agent_token_scan_worker
            result_path = self.agent_token_scan_result_path
            partial_path = result_path.with_suffix(".partial.json") if result_path is not None else None
            readable_path = (
                partial_path if partial_path is not None and partial_path.is_file()
                else result_path if result_path is not None and result_path.is_file()
                else None
            )
            if result is None and worker is not None and (readable_path is not None or not worker.is_alive()):
                worker_finished = not worker.is_alive()
                if worker_finished:
                    worker.join(timeout=0)
                try:
                    result = json.loads(readable_path.read_text(encoding="utf-8")) if readable_path is not None else None
                except (OSError, TypeError, ValueError) as exc:
                    if worker_finished:
                        result = {"error": f"agent token scan worker exited without a readable result: {exc}", "scan_id": self.agent_token_scan_id}
                finally:
                    if readable_path is not None:
                        try:
                            readable_path.unlink(missing_ok=True)
                        except OSError:
                            pass
                if worker_finished and not (isinstance(result, dict) and result.get("partial")):
                    self.agent_token_scan_worker = None
                    self.agent_token_scan_result_path = None
                self.agent_token_scan_result = result
            persistence = self.agent_token_scan_persistence
            if result is not None and persistence is None:
                self.agent_token_scan_result = None
                if result.get("partial") and self.agent_token_scan_includes_atoms:
                    # Counters are ready; let later counter-only scans proceed
                    # while this process finishes the historical atom spool.
                    self.agent_token_atom_worker = self.agent_token_scan_worker
                    self.agent_token_atom_result_path = self.agent_token_scan_result_path
                    self.agent_token_scan_worker = None
                    self.agent_token_scan_result_path = None
                    self.agent_token_scan_includes_atoms = False
                elif not result.get("partial"):
                    self.agent_token_scan_worker = None
                    self.agent_token_scan_result_path = None
                    self.agent_token_scan_includes_atoms = False
                scan_id = str(result.get("scan_id") or self.agent_token_scan_id)
            elif persistence is not None:
                scan_id = str(persistence["scan_id"])
            else:
                scan_id = ""
        if result is None and persistence is None:
            return self._drain_agent_token_atom_spool()
        if result is not None and persistence is None:
            error = result.get("error")
            if error:
                self.last_sampler_failure = redact_local_service_text(error)
                with self.agent_token_scan_lock:
                    self.agent_token_scan_completion = {"scan_id": scan_id, "response": {"ok": False, "error": str(error)}}
                return True
            if result.get("atoms_only"):
                try:
                    self._install_agent_token_atom_spool(result)
                except (OSError, RuntimeError, TypeError, ValueError, sqlite3.Error) as exc:
                    self.last_sampler_failure = redact_local_service_text(exc)
                return True
            try:
                claimed = self.claim_agent_token_deltas(
                    result["measurements"], result["seen_keys"], result["sample_time"],
                    fallback_state=result["previous_state"], persist_state=False,
                )
                self._install_agent_token_atom_spool(result)
                legacy_atom_records = result.get("atom_records")
                records = [
                    *claimed.get("records", []),
                    *(legacy_atom_records if isinstance(legacy_atom_records, list) else []),
                ]
                with self.agent_token_scan_lock:
                    self.agent_token_scan_persistence = {
                        "scan_id": scan_id, "records": records, "offset": 0,
                        "state": claimed["state"], "sample_time": result["sample_time"],
                        "changed": 0, "atom_count": int(result.get("atom_count") or 0),
                    }
                # Installing the pending result must itself stay cheap. The
                # next listener turn persists one page, leaving health/history
                # immediately serviceable when a large parser result arrives.
                return True
            except (OSError, RuntimeError, TypeError, ValueError, sqlite3.Error) as exc:
                self.last_sampler_failure = redact_local_service_text(exc)
                with self.agent_token_scan_lock:
                    self.agent_token_scan_completion = {"scan_id": scan_id, "response": {"ok": False, "error": str(exc)}}
                return True

        with self.agent_token_scan_lock:
            persistence = self.agent_token_scan_persistence
            assert persistence is not None
            offset = int(persistence["offset"])
            records = persistence["records"]
            page = records[offset:offset + STATSD_AGENT_TOKEN_PERSIST_BATCH_RECORDS]
        try:
            # Recovery records are already assigned to their retention tier
            # from their historical timestamps. Full-store compaction here
            # would turn every bounded page back into a multi-second listener
            # stall. Normal live maintenance keeps its existing compaction
            # path; recovery deliberately defers it.
            merged = self.merge_server_records(
                page, now=persistence["sample_time"], compact=False, refresh_rollups=False,
            ) if page else {"ok": True, "changed": 0}
        except (OSError, RuntimeError, TypeError, ValueError, sqlite3.Error) as exc:
            # Keep the exact pending cursor and uncommitted baseline. A later
            # listener turn retries the idempotent atom page instead of
            # skipping token history or advancing its counter baseline.
            self.last_sampler_failure = redact_local_service_text(exc)
            return True
        with self.agent_token_scan_lock:
            persistence = self.agent_token_scan_persistence
            if persistence is None:
                return True
            persistence["offset"] = offset + len(page)
            persistence["changed"] = int(persistence["changed"]) + int(merged.get("changed") or 0)
            if persistence["offset"] < len(persistence["records"]):
                return True
            self._set_agent_token_state(persistence["state"])
            response = {
                "ok": True, "records": [], "persisted_records": len(persistence["records"]),
                "spooled_atom_records": int(persistence.get("atom_count") or 0),
                "state": persistence["state"], "merge": {"ok": True, "changed": persistence["changed"]},
            }
            self.agent_token_scan_completion = {"scan_id": scan_id, "response": response}
            self.agent_token_scan_persistence = None
        return True

    def start_agent_token_scan_from_rows(
        self,
        rows: list[dict[str, Any]],
        seen_keys: set[str],
        sample_time: float,
        fallback_state: Any = None,
    ) -> dict[str, Any]:
        """Start one filesystem-only token scan and return without blocking RPC history."""

        self._drain_agent_token_scan_result()
        previous_state = self._agent_token_state(fallback_state)
        atom_state = self._agent_token_atom_state()
        with self.agent_token_scan_lock:
            if self.agent_token_scan_worker is not None or self.agent_token_scan_result is not None or self.agent_token_scan_persistence is not None:
                return {"ok": True, "accepted": False, "busy": True, "records": [], "state": previous_state}
            self.agent_token_scan_sequence += 1
            scan_id = f"scan-{self.agent_token_scan_sequence}"
            self.agent_token_scan_id = scan_id
            descriptor, result_path_text = tempfile.mkstemp(
                prefix="statsd-agent-token-scan-", suffix=".json", dir=self.socket_path.parent
            )
            os.close(descriptor)
            result_path = Path(result_path_text)
            # The child atomically replaces this path only once its complete
            # result is ready. Remove the empty reservation first so a failed
            # child is distinguishable from a valid empty scan.
            result_path.unlink(missing_ok=True)
            context = multiprocessing.get_context("spawn")
            include_atoms = self.agent_token_atom_worker is None and self.agent_token_atom_persistence is None
            worker = context.Process(
                target=_run_agent_token_scan_process,
                args=(
                    rows, sample_time, previous_state, seen_keys, scan_id, str(result_path),
                    atom_state, include_atoms,
                ),
                name="statsd-agent-token-scan",
                daemon=True,
            )
            self.agent_token_scan_worker = worker
            self.agent_token_scan_result_path = result_path
            self.agent_token_scan_includes_atoms = include_atoms
        try:
            worker.start()
        except RuntimeError:
            with self.agent_token_scan_lock:
                if self.agent_token_scan_worker is worker:
                    self.agent_token_scan_worker = None
                    self.agent_token_scan_result_path = None
                    self.agent_token_scan_includes_atoms = False
            try:
                result_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        return {"ok": True, "accepted": True, "busy": False, "scan_id": scan_id, "records": [], "state": previous_state}

    def finish_agent_token_scan(self, scan_id: str) -> dict[str, Any]:
        """Return one completed async claim while leaving history RPCs interleavable."""

        self._drain_agent_token_scan_result()
        with self.agent_token_scan_lock:
            completion = self.agent_token_scan_completion
            if completion is not None and completion.get("scan_id") == scan_id:
                self.agent_token_scan_completion = None
                response = completion.get("response")
                return {"done": True, **(response if isinstance(response, dict) else {"ok": False, "error": "invalid scan response"})}
            active = self.agent_token_scan_id == scan_id and (self.agent_token_scan_worker is not None or self.agent_token_scan_result is not None or self.agent_token_scan_persistence is not None)
        return {"ok": True, "done": False, "pending": active}

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
            total = PersistentStatsService._positive_finite(raw_item.get("total", raw_item.get("tokens")))
            samples = PersistentStatsService._positive_finite(raw_item.get("samples"))
            tokens = PersistentStatsService._positive_finite(raw_item.get("tokens", raw_item.get("total")))
            seconds = PersistentStatsService._positive_finite(raw_item.get("seconds"))
            raw_model_rates = raw_item.get("model_rates")
            if not isinstance(raw_model_rates, dict):
                raw_model_rates = {"unknown": {"total": total, "samples": samples, "tokens": tokens, "seconds": seconds}}
            model_rates: dict[str, dict[str, float]] = {}
            for raw_model, raw_rate in raw_model_rates.items():
                rate = raw_rate if isinstance(raw_rate, dict) else {"total": raw_rate}
                model = str(raw_model or "unknown").strip()[:256] or "unknown"
                model_total = PersistentStatsService._positive_finite(rate.get("total", rate.get("tokens")))
                model_samples = PersistentStatsService._positive_finite(rate.get("samples"))
                model_tokens = PersistentStatsService._positive_finite(rate.get("tokens", rate.get("total")))
                model_seconds = PersistentStatsService._positive_finite(rate.get("seconds"))
                if not model_total and not model_samples and not model_tokens:
                    continue
                model_rates[model] = {
                    "total": model_total,
                    "samples": model_samples or (1.0 if model_total or model_tokens else 0.0),
                    "tokens": model_tokens or model_total,
                    "seconds": model_seconds or seconds,
                }
            model_token_total = sum(float(rate.get("tokens") or 0.0) for rate in model_rates.values())
            if tokens <= 0:
                model_rates = {}
            elif model_token_total > tokens:
                scale = tokens / model_token_total
                for rate in model_rates.values():
                    rate["tokens"] = float(rate.get("tokens") or 0.0) * scale
                    rate["total"] = rate["tokens"]
            elif tokens > model_token_total:
                unknown = model_rates.setdefault("unknown", {"total": 0.0, "samples": 1.0, "tokens": 0.0, "seconds": seconds})
                unknown["tokens"] = float(unknown.get("tokens") or 0.0) + tokens - model_token_total
                unknown["total"] = unknown["tokens"]
            for rate in model_rates.values():
                rate["seconds"] = seconds
                rate["samples"] = 1.0 if float(rate.get("tokens") or 0.0) > 0 else 0.0
            records.append({
                "key": key,
                "label": str(raw_item.get("label") or key),
                "total": total,
                "samples": samples,
                "tokens": tokens,
                "seconds": seconds,
                "source": str(raw_item.get("source") or ""),
                "model_rates": model_rates,
            })
        return records

    @staticmethod
    def _merge_agent_token_model_rates(target: dict[str, Any], source: Any) -> None:
        target_rates = target.setdefault("model_rates", {})
        if not isinstance(target_rates, dict):
            target_rates = {}
            target["model_rates"] = target_rates
        if not isinstance(source, dict):
            return
        for raw_model, raw_rate in source.items():
            if not isinstance(raw_rate, dict):
                continue
            model = str(raw_model or "unknown").strip()[:256] or "unknown"
            rate = target_rates.setdefault(model, {"total": 0.0, "samples": 0.0, "tokens": 0.0, "seconds": 0.0})
            for field in ("total", "samples", "tokens", "seconds"):
                rate[field] = float(rate.get(field) or 0.0) + PersistentStatsService._positive_finite(raw_rate.get(field))

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
                "model_rates": copy.deepcopy(item.get("model_rates") or {}),
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
                target = target_rates.setdefault(item["key"], {"label": item["label"], "total": 0.0, "samples": 0.0, "tokens": 0.0, "seconds": 0.0, "source": "", "model_rates": {}})
                for field in ("total", "samples", "tokens", "seconds"):
                    target[field] = float(target.get(field) or 0.0) + float(item.get(field) or 0.0)
                target["label"] = item["label"]
                if item.get("source"):
                    target["source"] = str(item["source"])
                self._merge_agent_token_model_rates(target, item.get("model_rates"))
            self._recalculate_agent_token_totals(bucket)
        connection = self.store._connection()
        changed = False
        with connection:
            next_sequence = int(self.store.diagnostics().get("sequence") or 0)
            for key, recovered_bucket in recovered.items():
                existing = self.store.bucket(*key) or stats_store.empty_bucket(*key)
                target_rates = existing.setdefault("agent_token_rates", {})
                bucket_changed = False
                for item in self._agent_token_rate_records(recovered_bucket.get("agent_token_rates")):
                    existing_rate = target_rates.get(item["key"])
                    if isinstance(existing_rate, dict):
                        if existing_rate.get("model_rates"):
                            continue
                        existing_rate["model_rates"] = copy.deepcopy(item.get("model_rates") or {})
                        bucket_changed = bool(existing_rate["model_rates"]) or bucket_changed
                    else:
                        target_rates[item["key"]] = {
                            "label": item["label"],
                            "total": float(item.get("total") or 0.0),
                            "samples": float(item.get("samples") or 0.0),
                            "tokens": float(item.get("tokens") or 0.0),
                            "seconds": float(item.get("seconds") or 0.0),
                            "source": str(item.get("source") or "transcript"),
                            "model_rates": copy.deepcopy(item.get("model_rates") or {}),
                        }
                        bucket_changed = True
                if bucket_changed:
                    next_sequence += 1
                    existing["server_sequence"] = max(int(existing.get("server_sequence") or 0), next_sequence)
                    existing["sequence"] = max(int(existing.get("sequence") or 0), next_sequence)
                    self._recalculate_agent_token_totals(existing)
                    self.store._upsert_bucket(connection, existing)
                    changed = True
            for existing in self.store.all_buckets():
                before = json.dumps(existing.get("agent_token_rates") or {}, sort_keys=True, separators=(",", ":"))
                self._recalculate_agent_token_totals(existing)
                after = json.dumps(existing.get("agent_token_rates") or {}, sort_keys=True, separators=(",", ":"))
                if before == after:
                    continue
                next_sequence += 1
                existing["server_sequence"] = max(int(existing.get("server_sequence") or 0), next_sequence)
                existing["sequence"] = max(int(existing.get("sequence") or 0), next_sequence)
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
        if self.store.metadata_value(STATSD_AGENT_TOKEN_RECOVERY_MARKER) == str(STATSD_AGENT_TOKEN_RECOVERY_VERSION):
            return {"ok": True, "changed": False, "reason": "already_recovered"}
        sample_now = float(time.time() if now is None else now)
        if not rows:
            return {"ok": True, "changed": False, "reason": "no_transcript_rows"}
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
                    {str(event.model or "unknown").strip()[:256] or "unknown": event.tokens * covered_fraction},
                ))
        return self.recover_agent_token_history(records, now=sample_now)

    def _replace_backfill_source_atoms(self, source: str, atoms: list[dict[str, Any]], now: float) -> int:
        """Atomically replace one transcript source's staged retained atoms.

        The source marker is private migration provenance.  It deliberately
        does not match normal live records, and identity comparison against
        every retained component prevents a high-water handoff from charging a
        turn that live collection already persisted.
        """
        staged: dict[tuple[int, int], list[dict[str, Any]]] = {}
        for atom in atoms:
            projected = projected_usage_component(atom, self.pricing_catalog)
            if projected is None:
                continue
            duration = self._bucket_seconds(float(projected.get("timestamp") or now), now)
            start = int(math.floor(float(projected.get("timestamp") or now) / duration) * duration)
            staged.setdefault((start, duration), []).append(projected)
        # Backfill replays cumulative transcript snapshots, which can yield hundreds of
        # event atoms in one retained bucket.  The public cost schema already groups these
        # exact billable/source dimensions; coalesce them before persistence so one busy
        # minute cannot exceed the bounded SQLite JSON row contract.
        staged = {key: self._coalesced_backfill_components(values) for key, values in staged.items()}
        changed = 0
        next_sequence = int(self.store.diagnostics().get("sequence") or 0)
        connection = self.store._connection()
        with connection:
            existing = {(int(bucket["start"]), int(bucket["duration"])): bucket for bucket in self.store.all_buckets()}
            for key in set(existing) | set(staged):
                bucket = existing.get(key) or stats_store.empty_bucket(*key)
                summary = bucket.get("cost_summary") if isinstance(bucket.get("cost_summary"), dict) else {}
                old_components = [item for item in summary.get("components", []) if isinstance(item, dict)]
                retained_raw = [item for item in old_components if str(item.get("backfill_source") or "") != source]
                retained_live = [item for item in retained_raw if not str(item.get("backfill_source") or "")]
                retained_backfill = [item for item in retained_raw if str(item.get("backfill_source") or "")]
                # A prior interrupted migration may have persisted the old one-event-per-component
                # shape. Compact every retained backfill source while touching this bucket so the
                # repair itself cannot hit the row-size ceiling before reaching that source later.
                retained = [*retained_live, *self._coalesced_backfill_components(retained_backfill)]
                identities = {(str(item.get("event_id") or ""), str(item.get("direction") or ""), str(item.get("modality") or ""), str(item.get("cache_role") or ""), str(item.get("unit") or "")) for item in retained}
                for component in staged.get(key, []):
                    identity = (str(component.get("event_id") or ""), str(component.get("direction") or ""), str(component.get("modality") or ""), str(component.get("cache_role") or ""), str(component.get("unit") or ""))
                    if identity not in identities:
                        retained.append(component)
                        identities.add(identity)
                if retained == old_components:
                    continue
                replacement = dict(summary)
                self._recalculate_usage_summary(replacement, retained, legacy_output_only=not retained and bool(bucket.get("tokens_per_agent_total") or bucket.get("agent_token_rates")))
                self._apply_pricing_catalog_metadata(replacement)
                next_sequence += 1
                bucket["cost_summary"] = replacement
                bucket["server_sequence"] = max(int(bucket.get("server_sequence") or 0), next_sequence)
                bucket["sequence"] = max(int(bucket.get("sequence") or 0), next_sequence)
                self.store._upsert_bucket(connection, bucket)
                changed += 1
        if changed:
            self._encoded_query_cache.clear()
        return changed

    @staticmethod
    def _merge_coalesced_cost_components(
        grouped: dict[tuple[str, ...], dict[str, Any]], components: list[dict[str, Any]]
    ) -> None:
        """Add components to an exact response-only dimension accumulator."""
        for component in components:
            identity = tuple(
                str(component.get(field) if component.get(field) is not None else "")
                for field in STATS_COST_COMPONENT_DIMENSION_FIELDS
            )
            current = grouped.get(identity)
            if current is None:
                current = dict(component)
                current["event_id"] = "backfill:" + hashlib.sha256(
                    json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                ).hexdigest()
                current["call_id"] = ""
                current["quantity"] = 0.0
                current["micro_usd"] = 0
                current["lower_micro_usd"] = 0
                current["upper_micro_usd"] = 0
                current["estimated_lower_micro_usd"] = 0
                current["estimated_upper_micro_usd"] = 0
                grouped[identity] = current
            current["quantity"] += _usage_atom_number(component.get("quantity"))
            current["micro_usd"] += int(component.get("micro_usd") or 0)
            lower_micro_usd = _component_lower_micro_usd(component)
            upper_micro_usd = max(lower_micro_usd, _component_upper_micro_usd(component))
            current["lower_micro_usd"] += lower_micro_usd
            current["upper_micro_usd"] += upper_micro_usd
            current["estimated_lower_micro_usd"] += lower_micro_usd
            current["estimated_upper_micro_usd"] += upper_micro_usd

    @staticmethod
    def _coalesced_backfill_components(components: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, ...], dict[str, Any]] = {}
        PersistentStatsService._merge_coalesced_cost_components(grouped, components)
        return list(grouped.values())

    def migrate_usage_atom_history_from_rows(self, rows: list[dict[str, Any]], *, now: float | None = None) -> dict[str, Any]:
        """Replay retained transcript families into component storage once.

        The marker is written only after all currently discoverable rows have
        been merged.  Missing transcripts retain their legacy output history
        and therefore remain lower-bound/unpriced rather than being deleted.
        A later high-water/resume coordinator can call this before marking a
        complete roster; this first owner method is deliberately idempotent by
        stable event/component identity.
        """

        if self.store.metadata_value(STATSD_USAGE_ATOM_MIGRATION_MARKER) == str(STATSD_USAGE_ATOM_MIGRATION_VERSION):
            return {"ok": True, "changed": False, "reason": "already_migrated"}
        sample_now = float(time.time() if now is None else now)
        if not math.isfinite(sample_now):
            raise ValueError("now must be finite")
        self.store.set_metadata_value(
            STATSD_USAGE_ATOM_MIGRATION_STATUS_KEY,
            json.dumps({"state": "running", "started_at": sample_now, "sources_total": len(rows)}, sort_keys=True),
        )
        cutoff = sample_now - STATS_HISTORY_RETENTION_SECONDS
        missing = 0
        sources = 0
        changed = 0
        for row in rows:
            if not isinstance(row, dict):
                continue
            transcript = str(row.get("transcript") or "").strip()
            kind = str(row.get("kind") or "").strip().lower()
            if not transcript or kind not in {"codex", "claude"}:
                missing += 1
                continue
            path = Path(transcript)
            if not path.is_file():
                missing += 1
                continue
            sources += 1
            tmux_fields = _tmux_usage_fields_from_row(row, kind=kind)
            # The root transcript is the replay unit; descendants discovered
            # from it share this tag and are atomically replaced together.
            source = str(path.expanduser().resolve(strict=False))
            # Capture the complete discovered family and each file's byte
            # high-water mark before parsing.  A transcript that grows while
            # this bounded replay runs is subsequently collected by the live
            # sampler, never counted by both paths.
            family = session_files.claude_transcript_family_paths(path) if kind == "claude" else session_files.codex_transcript_family_paths(path)
            watermarks: dict[str, int] = {}
            for family_path in family:
                try:
                    watermarks[str(family_path.expanduser().resolve(strict=False))] = int(family_path.stat().st_size)
                except OSError:
                    # A vanished descendant is a partial source, not a reason
                    # to discard the captured siblings' durable atoms.
                    missing += 1
            source_atoms: list[dict[str, Any]] = []
            for atom in session_files.transcript_usage_atoms(path, kind, family_paths=family, max_bytes_by_path=watermarks):
                if atom.timestamp < cutoff or atom.timestamp > sample_now:
                    continue
                normalized = normalized_usage_atom(atom)
                if normalized is not None:
                    normalized["backfill_source"] = source
                    normalized.update(tmux_fields)
                    source_atoms.append(normalized)
            # One transaction replaces only this source's prior staged atoms.
            # Untagged live atoms are retained and event identity suppresses a
            # duplicate if the live sampler already observed the same turn.
            changed += self._replace_backfill_source_atoms(source, source_atoms, sample_now)
        # Output-only buckets predate billable atom telemetry.  Preserve their
        # existing chart data but mark the cost as a lower bound rather than
        # presenting an exact zero-dollar estimate.
        legacy_changed = self._mark_legacy_output_only_buckets(sample_now)
        # A partial roster must not consume the version marker: a future
        # startup can still recover a transcript that was temporarily absent.
        if missing:
            status = {"state": "partial", "updated_at": sample_now, "sources": sources, "missing": missing}
            self.store.set_metadata_value(STATSD_USAGE_ATOM_MIGRATION_STATUS_KEY, json.dumps(status, sort_keys=True))
            return {"ok": True, "changed": bool(changed or legacy_changed), "sources": sources, "missing": missing, "complete": False}
        self.store.set_metadata_value(STATSD_USAGE_ATOM_MIGRATION_MARKER, str(STATSD_USAGE_ATOM_MIGRATION_VERSION))
        self.store.set_metadata_value(
            STATSD_USAGE_ATOM_MIGRATION_STATUS_KEY,
            json.dumps({"state": "complete", "completed_at": sample_now, "sources": sources, "missing": 0}, sort_keys=True),
        )
        return {"ok": True, "changed": bool(changed or legacy_changed), "sources": sources, "missing": 0, "complete": True}

    def _usage_atom_migration_status(self) -> dict[str, Any]:
        """Expose bounded durable backfill state without scanning transcripts."""

        raw = self.store.metadata_value(STATSD_USAGE_ATOM_MIGRATION_STATUS_KEY)
        try:
            value = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            value = {}
        if not isinstance(value, dict):
            value = {}
        state = _usage_atom_text(value.get("state"), default="pending", limit=32)
        if self.store.metadata_value(STATSD_USAGE_ATOM_MIGRATION_MARKER) == str(STATSD_USAGE_ATOM_MIGRATION_VERSION):
            state = "complete"
        return {
            "state": state if state in {"pending", "running", "partial", "complete"} else "pending",
            "sources": max(0, int(_usage_atom_number(value.get("sources")))),
            "missing": max(0, int(_usage_atom_number(value.get("missing")))),
        }

    def _mark_legacy_output_only_buckets(self, now: float) -> int:
        """Mark retained counter-only history partial without touching totals."""
        cutoff = now - STATS_HISTORY_RETENTION_SECONDS
        changed = 0
        next_sequence = int(self.store.diagnostics().get("sequence") or 0)
        connection = self.store._connection()
        with connection:
            for bucket in self.store.all_buckets():
                if float(bucket.get("start") or 0) < cutoff:
                    continue
                summary = bucket.get("cost_summary") if isinstance(bucket.get("cost_summary"), dict) else {}
                components = summary.get("components") if isinstance(summary.get("components"), list) else []
                if components or not bool(bucket.get("tokens_per_agent_total") or bucket.get("agent_token_rates")):
                    continue
                replacement = dict(summary)
                self._recalculate_usage_summary(replacement, [], legacy_output_only=True)
                if replacement == summary:
                    continue
                next_sequence += 1
                bucket["cost_summary"] = replacement
                bucket["server_sequence"] = max(int(bucket.get("server_sequence") or 0), next_sequence)
                bucket["sequence"] = max(int(bucket.get("sequence") or 0), next_sequence)
                self.store._upsert_bucket(connection, bucket)
                changed += 1
        if changed:
            self._encoded_query_cache.clear()
        return changed

    def _compact_history(self, now: float) -> None:
        """Apply the bounded legacy retention tiers inside the durable owner."""
        sources = self.store.all_buckets()
        compacted: dict[tuple[int, int], dict[str, Any]] = {}
        cutoff = now - STATS_HISTORY_RETENTION_SECONDS
        changed = False
        for source in sources:
            if self._merge_usage_components(source, []):
                changed = True
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
            self.store.replace_buckets(
                [compacted[key] for key in sorted(compacted)],
                preserve_coverage=True,
            )
            self.store.retain_after(cutoff)

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
                    "model_rates": copy.deepcopy(value.get("model_rates") if isinstance(value.get("model_rates"), dict) else {}),
                }
                for key, value in sorted((bucket.get("agent_token_rates") or {}).items())
                if isinstance(value, dict)
            ]
            record["host_metrics"] = copy.deepcopy(bucket.get("host_metrics") if isinstance(bucket.get("host_metrics"), dict) else stats_store.empty_host_metrics())
            record["cost_summary"] = cost_summary_response(bucket.get("cost_summary"))
        return record

    def _merge_bucket(
        self,
        target: dict[str, Any],
        source: dict[str, Any],
        *,
        merge_cost_summary: bool = True,
        merge_agent_details: bool = True,
    ) -> None:
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
        if merge_agent_details:
            target_rates = target.setdefault("agent_token_rates", {})
            source_rates = source.get("agent_token_rates") if isinstance(source.get("agent_token_rates"), dict) else {}
            for rate_key, values in source_rates.items():
                if not isinstance(values, dict):
                    continue
                existing = target_rates.setdefault(str(rate_key), {"label": str(values.get("label") or rate_key), "total": 0.0, "samples": 0.0, "tokens": 0.0, "seconds": 0.0, "source": "", "model_rates": {}})
                for field in ("total", "samples", "tokens", "seconds"):
                    existing[field] = float(existing.get(field) or 0.0) + float(values.get(field) or 0.0)
                existing["label"] = str(values.get("label") or existing.get("label") or rate_key)
                if values.get("source"):
                    existing["source"] = str(values["source"])
                PersistentStatsService._merge_agent_token_model_rates(existing, values.get("model_rates"))
        if merge_cost_summary:
            target_summary = target.setdefault("cost_summary", {})
            source_summary = source.get("cost_summary") if isinstance(source.get("cost_summary"), dict) else {}
            target_components = target_summary.get("components") if isinstance(target_summary.get("components"), list) else []
            source_components = source_summary.get("components") if isinstance(source_summary.get("components"), list) else []
            seen_components = {
                (str(item.get("event_id") or ""), str(item.get("direction") or ""), str(item.get("modality") or ""), str(item.get("cache_role") or ""), str(item.get("unit") or ""))
                for item in target_components if isinstance(item, dict)
            }
            for item in source_components:
                if not isinstance(item, dict):
                    continue
                identity = (str(item.get("event_id") or ""), str(item.get("direction") or ""), str(item.get("modality") or ""), str(item.get("cache_role") or ""), str(item.get("unit") or ""))
                if identity in seen_components or len(target_components) >= STATS_COST_SUMMARY_MAX_COMPONENTS:
                    target_summary["lower_bound"] = True
                    target_summary["truncated"] = True
                    continue
                target_components.append(copy.deepcopy(item))
                seen_components.add(identity)
            target_summary["components"] = target_components
            self._recalculate_usage_summary(target_summary, target_components)
            target_summary["truncated"] = bool(target_summary.get("truncated")) or bool(source_summary.get("truncated"))
            target_summary["lower_bound"] = bool(target_summary.get("lower_bound")) or bool(source_summary.get("lower_bound")) or any(not item.get("priced") or not item.get("telemetry_complete") for item in target_components if isinstance(item, dict))
            try:
                target_summary["active_catalog_revision"] = max(
                    int(target_summary.get("active_catalog_revision") or 0),
                    int(source_summary.get("active_catalog_revision") or 0),
                )
            except (TypeError, ValueError):
                target_summary["active_catalog_revision"] = 0
            if source_summary.get("freshness"):
                target_summary["freshness"] = str(source_summary["freshness"])
            # Durable rollups retain the storage byte/count bounds and
            # transcript de-duplication enforced by the canonical projector.
            # Response-only history aggregation bypasses this branch and uses
            # its incremental dimension accumulator instead.
            self._merge_usage_components(target, [])
        source_metrics = source.get("host_metrics") if merge_agent_details and isinstance(source.get("host_metrics"), dict) else {}
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
        history_started = time.perf_counter()
        public_request = request.get("_internal_history") is not True
        if public_request:
            self.history_request_count += 1
        after_sequence = max(0, int(request.get("after_sequence", request.get("since", 0)) or 0))
        if request.get("include_history") is False:
            generation = self.store.latest_sequence()
            self.last_history_profile = {
                "cache_hit": False,
                "coverage_ms": 0.0,
                "query_ms": 0.0,
                "assemble_ms": round((time.perf_counter() - history_started) * 1000, 3),
                "source_records": 0,
                "returned_records": 0,
            }
            return {
                "sequence": after_sequence,
                "latest_sequence": generation,
                "agent_token_schema_version": STATS_AGENT_TOKEN_SCHEMA_VERSION,
                "records": [],
            }
        start = max(0, int(request.get("start") or 0))
        end = max(0, int(request.get("end") or 0))
        resolution = max(0, int(request.get("resolution_seconds") or 0))
        max_points = max(0, int(request.get("max_points") or 0))
        client_id = stats_history_client_id(request.get("client_id") or "")
        token_resolution = max(0, int(request.get("token_resolution_seconds", request.get("token_resolution", 0)) or 0))
        include_agent_tokens = bool(request.get("include_agent_tokens", token_resolution <= 0))
        generation = self.store.latest_sequence()
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
            if public_request:
                self.history_cache_hit_count += 1
            self.last_history_profile = {
                "cache_hit": True,
                "coverage_ms": 0.0,
                "query_ms": 0.0,
                "assemble_ms": round((time.perf_counter() - history_started) * 1000, 3),
                "source_records": int(cached[0].get("coverage", {}).get("source_records") or 0),
                "returned_records": len(cached[0].get("records") or []),
            }
            return copy.deepcopy(cached[0])
        # A live cursor is immutable: records before it cannot change the
        # returned delta. Range/zoom reads need the full bounded window to
        # aggregate exact totals at the requested resolution.
        coverage_started = time.perf_counter()
        coverage_facts = self.store.query_coverage(start=start, end=end)
        coverage_ms = (time.perf_counter() - coverage_started) * 1000
        available_start = coverage_facts["available_start"]
        available_end = coverage_facts["available_end"]
        retained_resolution = coverage_facts["retained_resolution"]
        effective_resolution = max(resolution, retained_resolution)
        if max_points:
            span = max(1, (end or available_end) - (start or available_start))
            effective_resolution = max(effective_resolution, math.ceil(span / max_points))
        # Live cursor deltas must remain raw so their sequence semantics are
        # exact. Persisted rollups serve bounded range/zoom reads only.
        persisted_rollup = (
            next((duration for duration in STATS_HISTORY_PERSISTED_ROLLUP_SECONDS if duration >= effective_resolution), 0)
            if end and effective_resolution >= STATS_HISTORY_PERSISTED_ROLLUP_SECONDS[1]
            else 0
        )
        query_started = time.perf_counter()
        source_buckets = (
            self.store.query_rollups(duration=persisted_rollup, start=start, end=end)
            if persisted_rollup and end
            else self.store.query_buckets(after_sequence=after_sequence if not end else 0, start=start, end=end)
        )
        # A new database has no historic rollups yet; range reads must stay
        # exact while incremental writes backfill the durable aggregate tiers.
        if persisted_rollup and end and not source_buckets:
            source_buckets = self.store.query_buckets(start=start, end=end)
            persisted_rollup = 0
        query_ms = (time.perf_counter() - query_started) * 1000
        if persisted_rollup:
            effective_resolution = persisted_rollup
        def encode_records(target_resolution: int) -> list[dict[str, Any]]:
            grouped: dict[tuple[int, int], dict[str, Any]] = {}
            cost_groups: dict[tuple[int, int], dict[tuple[str, ...], dict[str, Any]]] = {}
            cost_metadata: dict[tuple[int, int], dict[str, Any]] = {}
            for bucket in source_buckets:
                bucket_start, bucket_duration = int(bucket["start"]), int(bucket["duration"])
                if not target_resolution or bucket_duration >= target_resolution:
                    bucket_key = (bucket_start, bucket_duration)
                else:
                    anchor = start or 0
                    bucket_key = (anchor + ((bucket_start - anchor) // target_resolution) * target_resolution, target_resolution)
                target = grouped.setdefault(bucket_key, stats_store.empty_bucket(*bucket_key))
                # The public response needs the same exact component totals as
                # durable history, but repeatedly rebuilding a growing list is
                # quadratic for token-heavy ranges.  Accumulate billing
                # dimensions once and project the public summary after all
                # source buckets have been merged.
                self._merge_bucket(
                    target,
                    bucket,
                    merge_cost_summary=False,
                    merge_agent_details=include_agent_tokens,
                )
                if include_agent_tokens:
                    source_summary = bucket.get("cost_summary") if isinstance(bucket.get("cost_summary"), dict) else {}
                    source_components = [
                        item for item in source_summary.get("components", []) if isinstance(item, dict)
                    ]
                    self._merge_coalesced_cost_components(
                        cost_groups.setdefault(bucket_key, {}), source_components
                    )
                    metadata = cost_metadata.setdefault(bucket_key, {
                        "truncated": False,
                        "lower_bound": False,
                        "active_catalog_revision": 0,
                        "freshness": "",
                    })
                    metadata["truncated"] = bool(metadata["truncated"]) or bool(source_summary.get("truncated"))
                    metadata["lower_bound"] = bool(metadata["lower_bound"]) or bool(source_summary.get("lower_bound"))
                    try:
                        metadata["active_catalog_revision"] = max(
                            int(metadata["active_catalog_revision"] or 0),
                            int(source_summary.get("active_catalog_revision") or 0),
                        )
                    except (TypeError, ValueError):
                        pass
                    if source_summary.get("freshness"):
                        metadata["freshness"] = str(source_summary["freshness"])
            if include_agent_tokens:
                for bucket_key, target in grouped.items():
                    metadata = cost_metadata.get(bucket_key, {})
                    summary = target.setdefault("cost_summary", {})
                    summary["truncated"] = bool(metadata.get("truncated"))
                    components = list(cost_groups.get(bucket_key, {}).values())
                    self._recalculate_usage_summary(summary, components)
                    summary["lower_bound"] = bool(summary.get("lower_bound")) or bool(metadata.get("lower_bound"))
                    summary["active_catalog_revision"] = max(0, int(metadata.get("active_catalog_revision") or 0))
                    if metadata.get("freshness"):
                        summary["freshness"] = str(metadata["freshness"])
            return [
                self._record_from_bucket(bucket, client_id, include_agent_tokens=include_agent_tokens)
                for _key, bucket in sorted(grouped.items())
                if int(bucket.get("sequence") or 0) > after_sequence
            ]

        records = encode_records(effective_resolution)
        while max_points and len(records) > max_points:
            effective_resolution = max(effective_resolution + 1, math.ceil(effective_resolution * len(records) / max_points))
            records = encode_records(effective_resolution)
        intervals = coverage_facts["intervals"]
        covered_start = int(intervals[0]["start"]) if intervals else 0
        covered_end = int(intervals[-1]["end"]) if intervals else 0
        requested_start = start or covered_start
        requested_end = end or covered_end
        coverage_cursor = requested_start
        for interval in intervals:
            if int(interval["end"]) <= coverage_cursor:
                continue
            if int(interval["start"]) > coverage_cursor:
                break
            coverage_cursor = max(coverage_cursor, int(interval["end"]))
        interval_complete = bool(intervals and coverage_cursor >= requested_end)
        bounded_older = bool(end)
        coverage = {
            "mode": "older" if bounded_older else "live",
            "requested_start": start,
            "requested_end": end,
            "available_start": available_start,
            "available_end": available_end,
            "covered_start": covered_start,
            "covered_end": covered_end,
            "complete": interval_complete,
            "has_more_older": bool(available_start and covered_start and available_start < covered_start),
            "next_older_end": covered_start if available_start and covered_start and available_start < covered_start else 0,
            "resolution_seconds": effective_resolution,
            "source_resolution_seconds": retained_resolution,
            "max_points": max_points,
            "source_records": coverage_facts["source_records"],
            "returned_records": len(records),
            "cursor": after_sequence if bounded_older else generation,
            "latest_cursor": generation,
            "intervals": intervals,
            "store_intervals": coverage_facts["store_intervals"],
            "stores": coverage_facts["stores"],
            "epochs": coverage_facts["epochs"],
            "epochs_truncated": coverage_facts["epochs_truncated"],
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
            "usage_atom_backfill": self._usage_atom_migration_status(),
        }
        if token_resolution:
            token_request = {
                "_internal_history": True,
                "after_sequence": token_since,
                "start": token_history_start,
                "end": token_history_end,
                "resolution_seconds": max(STATS_AGENT_TOKEN_BUCKET_SECONDS, token_resolution),
                "max_points": max_points,
                "client_id": client_id,
                "include_agent_tokens": True,
            }
            token_payload = self._encoded_history(token_request)
            token_coverage = copy.deepcopy(token_payload["coverage"])
            token_facts = token_coverage.get("stores", {}).get("agent_tokens", {})
            token_intervals = list(token_facts.get("intervals") or [])
            token_coverage["intervals"] = token_intervals
            token_coverage["covered_start"] = int(token_facts.get("covered_start") or 0)
            token_coverage["covered_end"] = int(token_facts.get("covered_end") or 0)
            token_coverage["available_start"] = token_coverage["covered_start"]
            token_coverage["available_end"] = token_coverage["covered_end"]
            token_cursor = token_history_start or token_coverage["covered_start"]
            token_target = token_history_end or token_coverage["covered_end"]
            for interval in token_intervals:
                if int(interval["end"]) <= token_cursor:
                    continue
                if int(interval["start"]) > token_cursor:
                    break
                token_cursor = max(token_cursor, int(interval["end"]))
            token_coverage["complete"] = bool(token_intervals and token_cursor >= token_target)
            token_coverage["has_more_older"] = False
            token_coverage["next_older_end"] = 0
            payload["agent_token_history"] = {
                "sequence": token_payload["sequence"],
                "latest_sequence": token_payload["latest_sequence"],
                "records": [
                    {key: record[key] for key in ("start", "duration", "sequence", "tokens_per_agent_total", "agent_token_samples", "agent_token_rates", "cost_summary")}
                    for record in token_payload["records"]
                ],
                "resolution_seconds": token_request["resolution_seconds"],
                "snapshot": token_request["after_sequence"] == 0 and token_history_end <= 0,
                "coverage": token_coverage,
            }
        self._encoded_query_cache = {cache_key: (copy.deepcopy(payload), time.monotonic())}
        self.last_history_profile = {
            "cache_hit": False,
            "coverage_ms": round(coverage_ms, 3),
            "query_ms": round(query_ms, 3),
            "assemble_ms": round((time.perf_counter() - history_started) * 1000, 3),
            "source_records": len(source_buckets),
            "returned_records": len(records),
        }
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
        if query.get("include_history") is not False:
            payload["history"] = self._encoded_history(query)
        else:
            self.last_history_profile = {
                "cache_hit": False,
                "coverage_ms": 0.0,
                "query_ms": 0.0,
                "assemble_ms": 0.0,
                "source_records": 0,
                "returned_records": 0,
            }
        payload["shared_stats"] = shared_stats
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    def common_status(self) -> dict[str, Any]:
        with self.agent_token_scan_lock:
            scan_worker = self.agent_token_scan_worker
            scan_result_pending = self.agent_token_scan_result is not None
            scan_persisting = self.agent_token_scan_persistence is not None
            atom_worker = self.agent_token_atom_worker
            atom_persisting = self.agent_token_atom_persistence is not None
        scan_running = bool(scan_worker and scan_worker.is_alive())
        atom_scan_running = bool(atom_worker and atom_worker.is_alive())
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
            "queues": {"interactive": 0, "normal": 0, "maintenance": int(scan_result_pending or scan_persisting or atom_scan_running or atom_persisting) + int(self.pricing_reprojection is not None)},
            "active_task": "agent-token-scan" if scan_running else ("agent-token-persist" if scan_result_pending or scan_persisting else ("agent-token-atom-scan" if atom_scan_running else ("agent-token-atom-backfill" if atom_persisting else ("pricing-reprojection" if self.pricing_reprojection is not None else "")))),
            "cache": cache,
            "history_profile": dict(self.last_history_profile),
            "history_requests": self.history_request_count,
            "history_cache_hits": self.history_cache_hit_count,
            "last_success": self.last_client_at,
            "last_failure": last_failure,
            "last_sampler_success_at": self.last_sampler_success_at,
            "last_sampler_attempt_at": self.last_sampler_attempt_at,
            "agent_token_consumer_until": self.agent_token_consumer_until,
            "sampler_missed_cycles": self.sampler_missed_cycles,
            "sampler_late_cycles": self.sampler_late_cycles,
            "sampler_last_cycle_seconds": self.sampler_last_cycle_seconds,
            "sampler_alive": any(bool(item.get("alive")) for item in self.sampler_families.values()),
            "sampler_families": copy.deepcopy(self.sampler_families),
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
        try:
            requested_protocol = int(request.get("protocol_version") or STATSD_COMPAT_PROTOCOL_VERSION)
        except (TypeError, ValueError):
            requested_protocol = STATSD_COMPAT_PROTOCOL_VERSION
        response_protocol = STATSD_PROTOCOL_VERSION if requested_protocol >= STATSD_PROTOCOL_VERSION else STATSD_COMPAT_PROTOCOL_VERSION
        hot_version = {"version": STATSD_PROTOCOL_VERSION} if requested_protocol >= STATSD_PROTOCOL_VERSION else {}
        if action == "ping":
            return {"ok": True, "version": response_protocol, "pid": os.getpid(), "started_at": self.started_at}, b""
        if action == "status":
            return {**self.common_status(), "version": response_protocol}, b""
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
            return {"ok": True}, b""
        if action == "set_token_consumer_until":
            try:
                consumer_until = max(0.0, float(request.get("consumer_until") or 0.0))
            except (TypeError, ValueError):
                return {"ok": False, "error": "consumer_until must be a number"}, b""
            self.agent_token_consumer_until = max(self.agent_token_consumer_until, consumer_until)
            return {"ok": True, "agent_token_consumer_until": self.agent_token_consumer_until}, b""
        if action == "update_sampler_family":
            family = str(request.get("family") or "").strip()
            status = request.get("status")
            if not family or not isinstance(status, dict):
                return {"ok": False, "error": "family and status are required"}, b""
            self.sampler_families[family] = copy.deepcopy(status)
            successes = [float(item.get("last_success_at") or 0.0) for item in self.sampler_families.values()]
            attempts = [float(item.get("last_attempt_at") or 0.0) for item in self.sampler_families.values()]
            self.last_sampler_success_at = max(successes, default=0.0)
            self.last_sampler_attempt_at = max(attempts, default=0.0)
            self.sampler_last_cycle_seconds = max(
                (float(item.get("last_runtime_seconds") or 0.0) for item in self.sampler_families.values()),
                default=0.0,
            )
            self.sampler_late_cycles = sum(int(item.get("late_cycles") or 0) for item in self.sampler_families.values())
            self.sampler_missed_cycles = sum(int(item.get("missed_cycles") or 0) for item in self.sampler_families.values())
            failures = [
                f"{name}: {item.get('last_failure')}"
                for name, item in sorted(self.sampler_families.items())
                if str(item.get("last_failure") or "").strip()
            ]
            self.last_sampler_failure = "; ".join(failures)
            return {"ok": True, "family": family, **hot_version}, b""
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
                merged = self.merge_records(
                    [record for record in records if isinstance(record, dict)],
                    client_id=str(request.get("client_id") or ""),
                    now=request.get("now"),
                    clear=bool(request.get("clear")),
                    compact=request.get("compact") is not False,
                )
                return {**merged, **hot_version}, b""
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
                    compact=request.get("compact") is not False,
                )
                history = self._encoded_history(query)
            except (TypeError, ValueError, sqlite3.Error) as exc:
                return {"ok": False, "error": str(exc)}, b""
            return {"ok": True, "merged": merged, "history": history, **hot_version}, b""
        if action == "merge_server_records":
            records = request.get("records")
            if not isinstance(records, list):
                return {"ok": False, "error": "records must be a list"}, b""
            try:
                merged = self.merge_server_records(
                    [record for record in records if isinstance(record, dict)],
                    now=request.get("now"),
                    compact=request.get("compact") is not False,
                    refresh_rollups=request.get("refresh_rollups") is not False,
                )
                return {**merged, **hot_version}, b""
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
                return self.start_agent_token_scan_from_rows(
                    [item for item in rows if isinstance(item, dict)],
                    {str(key) for key in seen_keys},
                    float(request.get("sample_time") or 0.0),
                    fallback_state=request.get("fallback_state"),
                ), b""
            except (TypeError, ValueError, sqlite3.Error, OSError) as exc:
                return {"ok": False, "error": str(exc)}, b""
        if action == "finish_agent_token_scan":
            return self.finish_agent_token_scan(str(request.get("scan_id") or "")), b""
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
        if action == "migrate_usage_atom_history_from_rows":
            rows = request.get("rows")
            if not isinstance(rows, list):
                return {"ok": False, "error": "rows must be a list"}, b""
            try:
                return self.migrate_usage_atom_history_from_rows([row for row in rows if isinstance(row, dict)], now=request.get("now")), b""
            except (TypeError, ValueError, sqlite3.Error, OSError) as exc:
                return {"ok": False, "error": str(exc)}, b""
        if action == "reproject_cost_summaries":
            try:
                return self.reproject_cost_summaries(), b""
            except (TypeError, ValueError, sqlite3.Error, OSError) as exc:
                return {"ok": False, "error": str(exc)}, b""
        if action == "maybe_reproject_cost_summaries":
            try:
                return self._maybe_reproject_cost_summaries(), b""
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
        return run_local_rpc_service(
            socket_path=self.socket_path,
            lock_path=self.lock_path,
            service_name="statsd",
            stop_event=self.stop_event,
            handle=self.handle_with_binary,
            on_idle=self._idle_shutdown_ready,
            on_client=lambda: setattr(self, "last_client_at", monotonic_clock()),
            on_start=self._start_after_listener,
            on_shutdown=self._shutdown,
        )

    def _start_after_listener(self) -> None:
        """Open/migrate the durable store only after this daemon owns the lock."""
        self.import_legacy_history_once()
        self.agent_token_atom_persistence = self._load_agent_token_atom_spool()
        # Socket publication must mean it can answer immediately. The full
        # retained-history pass advances in bounded listener idle turns.
        self.rollup_backfill = {"after_start": -1, "after_duration": -1, "processed": 0}
        self._schedule_cost_reprojection()

    def _shutdown(self) -> None:
        self.stop_event.set()
        with self.agent_token_scan_lock:
            scan_worker = self.agent_token_scan_worker
            scan_result_path = self.agent_token_scan_result_path
            atom_worker = self.agent_token_atom_worker
            atom_result_path = self.agent_token_atom_result_path
        if scan_worker is not None and scan_worker.is_alive():
            scan_worker.join(timeout=1.0)
        if scan_worker is not None and scan_worker.is_alive():
            scan_worker.terminate()
            scan_worker.join(timeout=1.0)
        self._drain_agent_token_scan_result()
        if atom_worker is not None and atom_worker.is_alive():
            atom_worker.join(timeout=1.0)
        if atom_worker is not None and atom_worker.is_alive():
            atom_worker.terminate()
            atom_worker.join(timeout=1.0)
        if atom_worker is not None and not atom_worker.is_alive() and atom_result_path is not None and atom_result_path.is_file():
            self._drain_agent_token_atom_worker()
        if scan_result_path is not None:
            try:
                scan_result_path.unlink(missing_ok=True)
            except OSError:
                pass
        if self.agent_token_atom_result_path is not None:
            try:
                self.agent_token_atom_result_path.unlink(missing_ok=True)
                self.agent_token_atom_result_path.with_suffix(".partial.json").unlink(missing_ok=True)
            except OSError:
                pass
        self.store.close()


class StatsReaderService:
    """Read-only WAL peer for aggregation and JSON encoding."""

    READ_ACTIONS = {"history", "write_encoded_history", "write_encoded_sample"}

    def __init__(self, socket_path: Path, database_path: Path, *, idle_seconds: float = STATSD_DEFAULT_IDLE_SECONDS):
        self.socket_path = safe_socket_path(socket_path, prefix="yolomux-stats-reader")
        self.lock_path = self.socket_path.with_suffix(".lock")
        self.stop_event = threading.Event()
        self.idle_seconds = max(1.0, float(idle_seconds))
        self.started_at = time.time()
        self.last_client_at = monotonic_clock()
        self.last_failure = ""
        self.engine = PersistentStatsService(self.socket_path, database_path, pricing_catalog=None)
        self.engine.store = StatsStore(database_path, read_only=True)

    def handle_with_binary(self, request: dict[str, Any]) -> tuple[dict[str, Any], bytes]:
        action = str(request.get("action") or "")
        if action == "ping":
            return {
                "ok": True, "version": STATSD_PROTOCOL_VERSION,
                "pid": os.getpid(), "started_at": self.started_at,
            }, b""
        if action == "status":
            try:
                cache = self.engine.store.diagnostics()
            except sqlite3.Error as exc:
                cache = {"error": str(exc)}
                self.last_failure = str(exc)
            return {
                "ok": True, "version": STATSD_PROTOCOL_VERSION, "pid": os.getpid(),
                "started_at": self.started_at, "socket": str(self.socket_path),
                "cache": cache, "last_failure": self.last_failure,
                "queues": {"interactive": 0, "normal": 0, "maintenance": 0},
                "history_requests": self.engine.history_request_count,
                "history_cache_hits": self.engine.history_cache_hit_count,
            }, b""
        if action == "shutdown":
            self.stop_event.set()
            return {"ok": True}, b""
        if action not in self.READ_ACTIONS:
            return {"ok": False, "error": f"stats reader rejects action: {action}"}, b""
        return self.engine.handle_with_binary(request)

    def _idle_shutdown_ready(self) -> bool:
        return monotonic_clock() - self.last_client_at >= self.idle_seconds

    def run(self) -> int:
        return run_local_rpc_service(
            socket_path=self.socket_path,
            lock_path=self.lock_path,
            service_name="stats-reader",
            stop_event=self.stop_event,
            handle=self.handle_with_binary,
            on_idle=self._idle_shutdown_ready,
            on_client=lambda: setattr(self, "last_client_at", monotonic_clock()),
            on_start=self.engine.store.open,
            on_shutdown=self.engine.store.close,
        )


class StatsReaderClient(LocalServiceClient):
    def __init__(self, socket_path: Path, database_path: Path):
        self.database_path = Path(database_path)
        super().__init__(
            "stats-reader", "yolomux_lib.statsd", socket_path, STATSD_PROTOCOL_VERSION,
            idle_seconds=STATSD_DEFAULT_IDLE_SECONDS,
            extra_args=("--reader", "--database", str(self.database_path)),
        )

    def history(self, **request: Any) -> dict[str, Any]:
        if not self.ensure_started():
            return {"ok": False, "error": "stats reader unavailable"}
        return self.request({"action": "history", **request}, timeout=10.0)

    def runtime_status(self) -> dict[str, Any]:
        status = self.registry.status()
        payload = status.get("status") if isinstance(status.get("status"), dict) else {}
        pid = int(payload.get("pid") or 0)
        return {
            "service": "stats-reader", "pid": pid,
            "started_at": float(payload.get("started_at") or 0.0),
            "version": int(payload.get("version") or 0),
            "socket": str(payload.get("socket") or self.socket_path),
            "healthy": bool(status.get("healthy")),
            "cache": payload.get("cache") if isinstance(payload.get("cache"), dict) else {},
            "last_failure": str(payload.get("last_failure") or ""),
            "queues": payload.get("queues") if isinstance(payload.get("queues"), dict) else {},
            "history_requests": int(payload.get("history_requests") or 0),
            "history_cache_hits": int(payload.get("history_cache_hits") or 0),
            "resources": self.registry.resources(pid),
            "record": status.get("record") if isinstance(status.get("record"), dict) else {},
        }

    def encoded_history(self, **request: Any) -> tuple[dict[str, Any], bytes]:
        if not self.ensure_started():
            return {"ok": False, "error": "stats reader unavailable"}, b""
        return self.request_with_binary({"action": "write_encoded_history", **request}, timeout=10.0)

    def encoded_sample(self, sample: dict[str, Any], shared_stats: dict[str, Any], query: dict[str, Any]) -> tuple[dict[str, Any], bytes]:
        if not self.ensure_started():
            return {"ok": False, "error": "stats reader unavailable"}, b""
        return self.request_with_binary(
            {"action": "write_encoded_sample", "sample": sample, "shared_stats": shared_stats, "query": query},
            timeout=10.0,
        )


class StatsClient(LocalServiceClient):
    """Thin cross-port client for the ``statsd`` durable owner."""

    def __init__(
        self, socket_path: Path | None = None, database_path: Path | None = None,
        reader_socket_path: Path | None = None,
    ):
        self.database_path = Path(database_path or default_database_path())
        writer_socket = Path(socket_path or default_socket_path())
        super().__init__(
            "statsd",
            "yolomux_lib.statsd",
            writer_socket,
            STATSD_PROTOCOL_VERSION,
            idle_seconds=STATSD_DEFAULT_IDLE_SECONDS,
            extra_args=("--database", str(self.database_path), "--sampler-owner", str(default_sampler_owner_path(self.database_path.parent))),
        )
        reader_socket = reader_socket_path or (
            default_reader_socket_path()
            if socket_path is None
            else writer_socket.with_name(f"{writer_socket.stem}-reader{writer_socket.suffix}")
        )
        self.reader = StatsReaderClient(reader_socket, self.database_path)

    def request(self, payload: dict[str, Any], timeout: float = 0.5) -> dict[str, Any]:
        # A deliberate writer shutdown owns cleanup of its read peer too;
        # crashes remain independently recoverable through each registry.
        if str(payload.get("action") or "") == "shutdown":
            self.reader.request({"action": "shutdown"}, timeout=min(timeout, 0.5))
        return super().request(payload, timeout=timeout)

    def healthy(self) -> bool:
        response = self.request({"action": "ping", "protocol_version": STATSD_PROTOCOL_VERSION}, timeout=0.15)
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
            "agent_token_consumer_until": float(payload.get("agent_token_consumer_until") or 0.0),
            "sampler_missed_cycles": int(payload.get("sampler_missed_cycles") or 0),
            "sampler_late_cycles": int(payload.get("sampler_late_cycles") or 0),
            "sampler_last_cycle_seconds": float(payload.get("sampler_last_cycle_seconds") or 0.0),
            "history_profile": payload.get("history_profile") if isinstance(payload.get("history_profile"), dict) else {},
            "history_requests": int(payload.get("history_requests") or 0),
            "history_cache_hits": int(payload.get("history_cache_hits") or 0),
            "sampler_alive": payload.get("sampler_alive") is True,
            "sampler_families": payload.get("sampler_families") if isinstance(payload.get("sampler_families"), dict) else {},
            "restart_backoff_seconds": max(0.0, float(status.get("next_start_at") or 0.0) - monotonic_clock()),
            "generation": int(payload.get("generation") or 0), "record": status.get("record") if isinstance(status.get("record"), dict) else {},
            "resources": self.registry.resources(int(payload.get("pid") or 0)),
        }

    def history(self, **request: Any) -> dict[str, Any]:
        if not self.ensure_started():
            return {"ok": False, "error": "statsd unavailable"}
        return self.reader.history(**request)

    def merge_records(self, records: list[dict[str, Any]], *, client_id: str, now: float | None = None, clear: bool = False) -> dict[str, Any]:
        return self._request_live_or_start(
            {
                "action": "merge_records", "records": records, "client_id": client_id,
                "now": now, "clear": clear, "compact": False,
            },
            timeout=3.0,
        )

    def merge_and_history(self, records: list[dict[str, Any]], *, client_id: str, query: dict[str, Any], now: float | None = None, clear: bool = False) -> dict[str, Any]:
        merged = self.merge_records(records, client_id=client_id, now=now, clear=clear)
        if not merged.get("ok"):
            return merged
        history = self.reader.history(**query)
        if not history.get("ok"):
            return history
        return {"ok": True, "merged": merged, "history": history, "version": STATSD_PROTOCOL_VERSION}

    @staticmethod
    def _transport_requires_start(response: dict[str, Any]) -> bool:
        # The shared RPC client classifies connect failures before redaction.
        # Do not infer process death from application text or a busy timeout.
        return response.get("_transport_error") in {"absent", "refused"}

    def _request_live_or_start(self, payload: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        """Use the live socket first; contention is not evidence the daemon died."""

        versioned_payload = {**payload, "protocol_version": STATSD_PROTOCOL_VERSION}
        response = self.request(versioned_payload, timeout=timeout)
        try:
            response_version = int(response.get("version") or 0)
        except (TypeError, ValueError):
            response_version = 0
        if response.get("ok") is not False and response_version == STATSD_PROTOCOL_VERSION:
            return response
        should_start = self._transport_requires_start(response) or (
            response.get("ok") is not False and response_version != STATSD_PROTOCOL_VERSION
        )
        if not should_start:
            return response
        if not self.ensure_started():
            return response
        return self.request(versioned_payload, timeout=timeout)

    def merge_server_records(
        self,
        records: list[dict[str, Any]],
        *,
        now: float | None = None,
        compact: bool = False,
        refresh_rollups: bool = False,
        timeout: float = 3.0,
    ) -> dict[str, Any]:
        return self._request_live_or_start(
            {
                "action": "merge_server_records", "records": records, "now": now,
                "compact": compact, "refresh_rollups": refresh_rollups,
            },
            timeout=timeout,
        )

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
        response = self.request(
            {
                "action": "claim_agent_token_deltas_from_rows",
                "rows": rows,
                "seen_keys": sorted(seen_keys),
                "sample_time": sample_time,
                "fallback_state": fallback_state or {},
            },
            timeout=3.0,
        )
        scan_id = str(response.get("scan_id") or "")
        if not response.get("accepted") or not scan_id:
            return response
        deadline = monotonic_clock() + 15.0
        poll_wait = threading.Event()
        while monotonic_clock() < deadline:
            completed = self.request({"action": "finish_agent_token_scan", "scan_id": scan_id}, timeout=1.0)
            if completed.get("done"):
                return completed
            poll_wait.wait(0.01)
        return {**response, "pending": True}

    def recover_agent_token_history(self, records: list[dict[str, Any]], *, now: float) -> dict[str, Any]:
        if not self.ensure_started():
            return {"ok": False, "error": "statsd unavailable"}
        return self.request({"action": "recover_agent_token_history", "records": records, "now": now}, timeout=3.0)

    def recover_agent_token_history_from_rows(self, rows: list[dict[str, Any]], *, now: float) -> dict[str, Any]:
        if not self.ensure_started():
            return {"ok": False, "error": "statsd unavailable"}
        return self.request({"action": "recover_agent_token_history_from_rows", "rows": rows, "now": now}, timeout=3.0)

    def migrate_usage_atom_history_from_rows(self, rows: list[dict[str, Any]], *, now: float) -> dict[str, Any]:
        if not self.ensure_started():
            return {"ok": False, "error": "statsd unavailable"}
        return self.request({"action": "migrate_usage_atom_history_from_rows", "rows": rows, "now": now}, timeout=10.0)

    def reproject_cost_summaries(self) -> dict[str, Any]:
        if not self.ensure_started():
            return {"ok": False, "error": "statsd unavailable"}
        return self.request({"action": "reproject_cost_summaries"}, timeout=10.0)

    def maybe_reproject_cost_summaries(self) -> dict[str, Any]:
        return self.request({"action": "maybe_reproject_cost_summaries"}, timeout=10.0)

    def set_token_consumer_until(self, consumer_until: float) -> dict[str, Any]:
        if not self.ensure_started():
            return {"ok": False, "error": "statsd unavailable"}
        return self.request({"action": "set_token_consumer_until", "consumer_until": consumer_until}, timeout=1.0)

    def update_sampler_family(self, family: str, status: dict[str, Any]) -> dict[str, Any]:
        return self._request_live_or_start(
            {"action": "update_sampler_family", "family": family, "status": status}, timeout=1.0,
        )

    def encoded_history(self, **request: Any) -> tuple[dict[str, Any], bytes]:
        if not self.ensure_started():
            return {"ok": False, "error": "statsd unavailable"}, b""
        return self.reader.encoded_history(**request)

    def encoded_sample(self, sample: dict[str, Any], shared_stats: dict[str, Any], *, query: dict[str, Any]) -> tuple[dict[str, Any], bytes]:
        if not self.ensure_started():
            return {"ok": False, "error": "statsd unavailable"}, b""
        return self.reader.encoded_sample(sample, shared_stats, query)

    def replace_and_encoded_history(self, buckets: list[dict[str, Any]], **query: Any) -> tuple[dict[str, Any], bytes]:
        if not self.ensure_started():
            return {"ok": False, "error": "statsd unavailable"}, b""
        replaced = self.request({"action": "replace_buckets", "buckets": buckets}, timeout=3.0)
        if not replaced.get("ok"):
            return replaced, b""
        return self.reader.encoded_history(**query)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="YOLOmux persistent stats service")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--reader", action="store_true")
    parser.add_argument("--socket", default=str(default_socket_path()))
    parser.add_argument("--database", default=str(default_database_path()))
    parser.add_argument("--sampler-owner", default=str(default_sampler_owner_path()))
    parser.add_argument("--idle-seconds", type=float, default=STATSD_DEFAULT_IDLE_SECONDS)
    args = parser.parse_args(argv)
    if not args.serve:
        parser.error("--serve is required")
    apply_service_process_priority()
    if args.reader:
        return StatsReaderService(
            Path(args.socket), Path(args.database), idle_seconds=args.idle_seconds,
        ).run()
    # The daemon, rather than an individual web process, owns projection onto
    # the shared catalog.  Direct unit-service construction can still inject a
    # fake catalog or leave it absent without touching a developer's cache.
    return PersistentStatsService(
        Path(args.socket),
        Path(args.database),
        idle_seconds=args.idle_seconds,
        sampler_owner_path=Path(args.sampler_owner),
        pricing_catalog=PricingCatalog(),
    ).run()


if __name__ == "__main__":
    raise SystemExit(main())
