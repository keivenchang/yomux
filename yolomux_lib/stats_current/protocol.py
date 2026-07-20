# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Small, strict current-only wire contract for YO!stats."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, TypedDict, cast

from . import identity
from . import resolution as resolution_policy

WIRE_PROTOCOL_VERSION = 2
MAX_APPEND_RECORDS = 1_000
MAX_SAFE_INTEGER = (1 << 53) - 1
MAX_CLIENT_ID_BYTES = 128
MAX_SOURCE_ID_BYTES = 256
MAX_RETRY_AFTER_SECONDS = 60
MAX_DELTA_IDENTITIES = 2 * resolution_policy.MAX_BUCKETS
COST_REPORT_SCHEMA_VERSION = 2
COST_REPORT_DIMENSIONS = ("input", "cache_read", "cache_write", "output", "other")
MAX_COST_DETAIL_MODELS = 16
MAX_COST_DETAIL_AGENTS = 16
MAX_COST_DETAIL_EVIDENCE = 32
SNAPSHOT_REQUEST_FIELDS = frozenset(
    {"range_seconds", "resolution", "client_id", "since_generation"}
)
DELTA_REQUEST_FIELDS = frozenset(
    {
        "range_seconds",
        "resolution_seconds",
        "client_id",
        "after_cache_generation",
        "after_revision",
    }
)
RETIRED_REQUEST_FIELDS = frozenset(
    {
        "history",
        "history_start",
        "history_end",
        "history_resolution",
        "history_max_points",
        "max_points",
        "token_resolution",
        "token_start",
        "token_end",
        "cursor",
        "page",
    }
)

RequestedResolution = int | Literal["AUTO"]


class SnapshotWire(TypedDict):
    protocol_version: int
    range_seconds: int
    requested_resolution: RequestedResolution
    resolution_seconds: int
    window_start: int
    window_end: int
    generated_at: int | float
    source_generation: int
    cache_generation: int
    rightmost_open: bool
    buckets: list[dict[str, object]]
    no_data: list[dict[str, object]]
    cost_report: dict[str, object]


class DeltaWire(TypedDict):
    protocol_version: int
    range_seconds: int
    resolution_seconds: int
    source_generation: int
    base_cache_generation: int
    cache_generation: int
    revision: int
    buckets: list[dict[str, object]]
    no_data: list[dict[str, object]]
    tombstones: list[dict[str, object]]
    cost_report: dict[str, object]


class PendingWire(TypedDict):
    status: Literal["pending"]
    protocol_version: int
    range_seconds: int
    requested_resolution: RequestedResolution
    resolution_seconds: int
    retry_after_seconds: int
    reason: str


class UnsupportedWire(TypedDict):
    status: Literal["unsupported"]
    protocol_version: int
    reason: str
    range_seconds: int | None
    valid_ranges: list[int]
    valid_resolutions: list[int | str]


class UpgradeRequiredWire(TypedDict):
    status: Literal["upgrade_required"]
    protocol_version: int
    required_protocol_version: int
    required_schema_generation: int
    required_build: str
    reason: str


class ProtocolValidationError(ValueError):
    """A request or response violates the one current protocol."""


class UnsupportedRequest(ProtocolValidationError):
    def __init__(self, response: UnsupportedWire):
        self.response = response
        super().__init__(response["reason"])


def _integer(value: object, name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ProtocolValidationError(f"{name} must be an integer >= {minimum}")
    return value


def _generation(value: object, name: str, *, minimum: int = 0) -> int:
    generation = _integer(value, name, minimum=minimum)
    if generation > MAX_SAFE_INTEGER:
        raise ProtocolValidationError(f"{name} exceeds the exact JSON integer range")
    return generation


def _number(value: object, name: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ProtocolValidationError(f"{name} must be a non-negative finite number")
    return value


def _query_integer(value: object, name: str, *, minimum: int = 0) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return _integer(value, name, minimum=minimum)
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal():
        raise ProtocolValidationError(f"{name} must be a canonical decimal integer")
    parsed = int(value)
    if str(parsed) != value or parsed < minimum:
        raise ProtocolValidationError(f"{name} must be a canonical decimal integer >= {minimum}")
    return parsed


def _query_generation(value: object, name: str) -> int:
    generation = _query_integer(value, name)
    if generation > MAX_SAFE_INTEGER:
        raise ProtocolValidationError(f"{name} exceeds the exact JSON integer range")
    return generation


def _client_id(value: object) -> str:
    try:
        return identity.identity_text(
            value, "client_id", maximum_bytes=MAX_CLIENT_ID_BYTES,
        )
    except identity.IdentityValidationError as error:
        raise ProtocolValidationError(str(error)) from error


def _fields(value: object, name: str, expected: set[str]) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ProtocolValidationError(f"{name} must be an object with string fields")
    actual = set(value)
    if actual != expected:
        missing, unknown = expected - actual, actual - expected
        detail = []
        if missing:
            detail.append(f"missing {sorted(missing)}")
        if unknown:
            detail.append(f"unknown {sorted(unknown)}")
        raise ProtocolValidationError(f"{name} has {' and '.join(detail)} fields")
    return value


def _json(value: object, name: str) -> None:
    if value is None or isinstance(value, (bool, int, str)):
        return
    if isinstance(value, float):
        if math.isfinite(value):
            return
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _json(item, f"{name}[{index}]")
        return
    elif isinstance(value, Mapping) and all(isinstance(key, str) for key in value):
        for key, item in value.items():
            _json(item, f"{name}.{key}")
        return
    raise ProtocolValidationError(f"{name} is not finite JSON data")


def _requested(value: object, *, query: bool = False) -> RequestedResolution:
    if value == resolution_policy.AUTO:
        return resolution_policy.AUTO
    return _query_integer(value, "resolution", minimum=1) if query else _integer(value, "requested_resolution", minimum=1)


def _key(range_value: object, requested_value: object, concrete_value: object) -> tuple[int, RequestedResolution, int]:
    range_seconds = _integer(range_value, "range_seconds", minimum=1)
    requested = _requested(requested_value)
    concrete = _integer(concrete_value, "resolution_seconds", minimum=1)
    try:
        expected = resolution_policy.resolve_requested(range_seconds, requested)
    except ValueError as error:
        raise ProtocolValidationError(str(error)) from error
    if concrete != expected:
        raise ProtocolValidationError("concrete resolution does not match the request")
    return range_seconds, requested, concrete


@dataclass(frozen=True, slots=True)
class SnapshotRequest:
    range_seconds: int
    resolution: RequestedResolution
    resolution_seconds: int
    client_id: str
    since_generation: int | None


@dataclass(frozen=True, slots=True)
class DeltaRequest:
    range_seconds: int
    resolution_seconds: int
    client_id: str
    after_cache_generation: int
    after_revision: int


def unsupported_response(reason: str, range_seconds: int | None = None) -> UnsupportedWire:
    valid = resolution_policy.explicit_resolutions(range_seconds) if range_seconds in resolution_policy.RANGE_SECONDS else resolution_policy.RESOLUTION_CHOICES
    return {
        "status": "unsupported",
        "protocol_version": WIRE_PROTOCOL_VERSION,
        "reason": reason,
        "range_seconds": range_seconds,
        "valid_ranges": list(resolution_policy.RANGE_SECONDS),
        "valid_resolutions": [resolution_policy.AUTO, *valid],
    }


def parse_snapshot_request(params: Mapping[str, object]) -> SnapshotRequest:
    """Reject aliases and coercions instead of translating an old request."""
    if not isinstance(params, Mapping) or any(not isinstance(key, str) for key in params):
        raise UnsupportedRequest(unsupported_response("query parameters must be an object"))
    keys = set(params)
    rejected = keys - SNAPSHOT_REQUEST_FIELDS
    if rejected:
        kind = "retired" if rejected & RETIRED_REQUEST_FIELDS else "unknown"
        raise UnsupportedRequest(unsupported_response(f"{kind} query parameters: {sorted(rejected)}"))
    missing = {"range_seconds", "resolution", "client_id"} - keys
    if missing:
        raise UnsupportedRequest(unsupported_response(f"missing query parameters: {sorted(missing)}"))
    range_seconds: int | None = None
    try:
        range_seconds = _query_integer(params["range_seconds"], "range_seconds", minimum=1)
        requested = _requested(params["resolution"], query=True)
        concrete = resolution_policy.resolve_requested(range_seconds, requested)
        client_id = _client_id(params["client_id"])
        since = _query_generation(params["since_generation"], "since_generation") if "since_generation" in params else None
        return SnapshotRequest(range_seconds, requested, concrete, client_id, since)
    except (ProtocolValidationError, ValueError) as error:
        raise UnsupportedRequest(unsupported_response(str(error), range_seconds)) from error


def _unsupported_delta(reason: str, range_seconds: int | None = None) -> UnsupportedWire:
    response = unsupported_response(reason, range_seconds)
    if range_seconds in resolution_policy.RANGE_SECONDS:
        response["valid_resolutions"] = list(resolution_policy.explicit_resolutions(range_seconds))
    else:
        response["valid_resolutions"] = list(resolution_policy.RESOLUTION_CHOICES)
    return response


def parse_delta_request(params: Mapping[str, object]) -> DeltaRequest:
    """Validate the sole exact-delta cursor; AUTO and legacy aliases are not delta keys."""
    if not isinstance(params, Mapping) or any(not isinstance(key, str) for key in params):
        raise UnsupportedRequest(_unsupported_delta("delta parameters must be an object"))
    keys = set(params)
    rejected = keys - DELTA_REQUEST_FIELDS
    if rejected:
        kind = "retired" if rejected & RETIRED_REQUEST_FIELDS else "unknown"
        raise UnsupportedRequest(_unsupported_delta(f"{kind} delta parameters: {sorted(rejected)}"))
    missing = DELTA_REQUEST_FIELDS - keys
    if missing:
        raise UnsupportedRequest(_unsupported_delta(f"missing delta parameters: {sorted(missing)}"))
    range_seconds: int | None = None
    try:
        range_seconds = _query_integer(params["range_seconds"], "range_seconds", minimum=1)
        resolution_seconds = _query_integer(
            params["resolution_seconds"], "resolution_seconds", minimum=1
        )
        if not resolution_policy.is_supported(range_seconds, resolution_seconds):
            raise ProtocolValidationError("delta has an unsupported Range/Resolution key")
        return DeltaRequest(
            range_seconds,
            resolution_seconds,
            _client_id(params["client_id"]),
            _query_generation(params["after_cache_generation"], "after_cache_generation"),
            _query_generation(params["after_revision"], "after_revision"),
        )
    except (ProtocolValidationError, ValueError) as error:
        raise UnsupportedRequest(_unsupported_delta(str(error), range_seconds)) from error


BUCKET_FIELDS = {"start", "duration", "series", "source", "open"}
SOURCE_FIELDS = {"first_timestamp", "last_timestamp", "count"}
SERIES_VALUE_FIELDS = {"value", "source_count", "first_timestamp", "last_timestamp"}
NO_DATA_FIELDS = {"family", "source_id", "start", "end", "epoch", "reason", "source_cadence_seconds"}
BUCKET_TOMBSTONE_FIELDS = {"kind", "start", "duration"}
NO_DATA_TOMBSTONE_FIELDS = {"kind", "family", "source_id", "start", "end", "epoch"}
SNAPSHOT_FIELDS = {"protocol_version", "range_seconds", "requested_resolution", "resolution_seconds", "window_start", "window_end", "generated_at", "source_generation", "cache_generation", "rightmost_open", "buckets", "no_data", "cost_report"}
DELTA_FIELDS = {"protocol_version", "range_seconds", "resolution_seconds", "source_generation", "base_cache_generation", "cache_generation", "revision", "buckets", "no_data", "tombstones", "cost_report"}

COST_REPORT_FIELDS = {
    "schema_version", "total_micro_usd", "total_api_list_micro_usd",
    "total_tokens", "dimensions", "priced", "unpriced", "models", "agents",
    "evidence", "catalog_revision", "omissions", "reasoning_available",
}
COST_DIMENSION_FIELDS = {"tokens", "micro_usd", "api_list_micro_usd"}
COST_COVERAGE_FIELDS = {"atoms", "tokens"}
COST_MODEL_FIELDS = {
    "key", "provider", "model", "total_tokens", "total_micro_usd",
    "total_api_list_micro_usd", "dimensions", "priced", "unpriced",
}
COST_AGENT_FIELDS = {
    "key", "source", "label", "total_tokens", "total_micro_usd",
    "total_api_list_micro_usd", "dimensions", "priced", "unpriced",
}
COST_EVIDENCE_FIELDS = {
    "key", "provider", "model", "dimension", "direction", "modality", "cache_role",
    "unit", "pricing_profile", "service_tier", "catalog_model", "rate_usd", "rate_scale",
    "effective_from", "source_kind", "source_url", "catalog_revision", "tokens",
    "micro_usd", "api_list_micro_usd", "priced_atoms",
}
COST_OMISSION_FIELDS = {"models", "agents", "evidence"}

BucketIdentity = tuple[Literal["bucket"], int, int]
NoDataIdentity = tuple[
    Literal["no_data"], str, str, str, int | float, int | float
]
DeltaIdentity = BucketIdentity | NoDataIdentity


def _identity_text(value: object, name: str, *, maximum_bytes: int = 256) -> str:
    try:
        return identity.identity_text(value, name, maximum_bytes=maximum_bytes)
    except identity.IdentityValidationError as error:
        raise ProtocolValidationError(str(error)) from error


def _cost_integer(value: object, name: str) -> int:
    return _generation(value, name)


def _cost_dimensions(value: object, name: str) -> Mapping[str, object]:
    dimensions = _fields(value, name, set(COST_REPORT_DIMENSIONS))
    for dimension in COST_REPORT_DIMENSIONS:
        item = _fields(
            dimensions[dimension], f"{name}.{dimension}", COST_DIMENSION_FIELDS,
        )
        _cost_integer(item["tokens"], f"{name}.{dimension}.tokens")
        _cost_integer(item["micro_usd"], f"{name}.{dimension}.micro_usd")
        _cost_integer(
            item["api_list_micro_usd"], f"{name}.{dimension}.api_list_micro_usd",
        )
    return dimensions


def _cost_coverage(value: object, name: str) -> Mapping[str, object]:
    coverage = _fields(value, name, COST_COVERAGE_FIELDS)
    _cost_integer(coverage["atoms"], f"{name}.atoms")
    _cost_integer(coverage["tokens"], f"{name}.tokens")
    return coverage


def _cost_key(value: object, name: str) -> str:
    key = _identity_text(value, name, maximum_bytes=24)
    if len(key) != 24 or any(char not in "0123456789abcdef" for char in key):
        raise ProtocolValidationError(f"{name} must be a 24-character lowercase hex key")
    return key


def _cost_text(value: object, name: str, *, allow_empty: bool = False) -> str:
    if allow_empty and value == "":
        return ""
    return _identity_text(value, name, maximum_bytes=MAX_SOURCE_ID_BYTES)


def _validate_cost_attribution_row(
    value: object,
    name: str,
    fields: set[str],
) -> tuple[str, int, int, int]:
    row = _fields(value, name, fields)
    key = _cost_key(row["key"], f"{name}.key")
    dimensions = _cost_dimensions(row["dimensions"], f"{name}.dimensions")
    total_tokens = _cost_integer(row["total_tokens"], f"{name}.total_tokens")
    total_micro_usd = _cost_integer(
        row["total_micro_usd"], f"{name}.total_micro_usd",
    )
    total_api_list_micro_usd = _cost_integer(
        row["total_api_list_micro_usd"], f"{name}.total_api_list_micro_usd",
    )
    dimension_tokens = sum(
        cast(Mapping[str, object], dimensions[dimension])["tokens"]
        for dimension in COST_REPORT_DIMENSIONS
    )
    dimension_cost = sum(
        cast(Mapping[str, object], dimensions[dimension])["micro_usd"]
        for dimension in COST_REPORT_DIMENSIONS
    )
    dimension_api_list_cost = sum(
        cast(Mapping[str, object], dimensions[dimension])["api_list_micro_usd"]
        for dimension in COST_REPORT_DIMENSIONS
    )
    if (dimension_tokens, dimension_cost, dimension_api_list_cost) != (
        total_tokens, total_micro_usd, total_api_list_micro_usd,
    ):
        raise ProtocolValidationError(f"{name} totals disagree with its dimensions")
    priced = _cost_coverage(row["priced"], f"{name}.priced")
    unpriced = _cost_coverage(row["unpriced"], f"{name}.unpriced")
    if priced["tokens"] + unpriced["tokens"] != total_tokens:
        raise ProtocolValidationError(f"{name} priced and unpriced tokens disagree with total")
    return key, total_tokens, total_micro_usd, total_api_list_micro_usd


def _cost_rows(
    value: object,
    name: str,
    fields: set[str],
    maximum: int,
) -> list[dict[str, object]]:
    if not isinstance(value, list) or len(value) > maximum:
        raise ProtocolValidationError(f"{name} must be an array of at most {maximum} rows")
    previous: tuple[int, int, int, str] | None = None
    keys: set[str] = set()
    for index, item in enumerate(value):
        row = _fields(item, f"{name}[{index}]", fields)
        key, total_tokens, total_micro_usd, total_api_list_micro_usd = _validate_cost_attribution_row(
            row, f"{name}[{index}]", fields,
        )
        if key in keys:
            raise ProtocolValidationError(f"{name} keys must be unique")
        keys.add(key)
        order = (-total_tokens, -total_api_list_micro_usd, -total_micro_usd, key)
        if previous is not None and order <= previous:
            raise ProtocolValidationError(f"{name} rows must use deterministic rank order")
        previous = order
        if fields == COST_MODEL_FIELDS:
            _cost_text(row["provider"], f"{name}[{index}].provider")
            _cost_text(row["model"], f"{name}[{index}].model")
        else:
            _cost_text(row["source"], f"{name}[{index}].source")
            _cost_text(row["label"], f"{name}[{index}].label")
    return cast(list[dict[str, object]], value)


def _cost_evidence(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list) or len(value) > MAX_COST_DETAIL_EVIDENCE:
        raise ProtocolValidationError(
            f"cost_report.evidence must be an array of at most {MAX_COST_DETAIL_EVIDENCE} rows"
        )
    previous: tuple[int, int, int, str] | None = None
    keys: set[str] = set()
    for index, raw in enumerate(value):
        name = f"cost_report.evidence[{index}]"
        row = _fields(raw, name, COST_EVIDENCE_FIELDS)
        key = _cost_key(row["key"], f"{name}.key")
        if key in keys:
            raise ProtocolValidationError("cost_report.evidence keys must be unique")
        keys.add(key)
        tokens = _cost_integer(row["tokens"], f"{name}.tokens")
        micro_usd = _cost_integer(row["micro_usd"], f"{name}.micro_usd")
        api_list_micro_usd = _cost_integer(
            row["api_list_micro_usd"], f"{name}.api_list_micro_usd",
        )
        _cost_integer(row["priced_atoms"], f"{name}.priced_atoms")
        _cost_integer(row["rate_scale"], f"{name}.rate_scale")
        _cost_integer(row["catalog_revision"], f"{name}.catalog_revision")
        for field in COST_EVIDENCE_FIELDS - {
            "key", "tokens", "micro_usd", "api_list_micro_usd", "priced_atoms", "rate_scale",
            "catalog_revision", "source_url",
        }:
            _cost_text(row[field], f"{name}.{field}")
        source_url = _cost_text(row["source_url"], f"{name}.source_url", allow_empty=True)
        if source_url and not source_url.startswith(("https://", "http://")):
            raise ProtocolValidationError(f"{name}.source_url must be an HTTP(S) URL")
        if row["dimension"] not in COST_REPORT_DIMENSIONS:
            raise ProtocolValidationError(f"{name}.dimension is not current")
        order = (-tokens, -api_list_micro_usd, -micro_usd, key)
        if previous is not None and order <= previous:
            raise ProtocolValidationError(
                "cost_report.evidence rows must use deterministic rank order"
            )
        previous = order
    return cast(list[dict[str, object]], value)


def validate_cost_report(value: object) -> dict[str, object]:
    """Validate the complete precomputed range report carried by snapshots and deltas."""

    report = _fields(value, "cost_report", COST_REPORT_FIELDS)
    if report["schema_version"] != COST_REPORT_SCHEMA_VERSION:
        raise ProtocolValidationError("unsupported cost_report schema_version")
    if report["reasoning_available"] is not False:
        raise ProtocolValidationError("reasoning tokens are not represented by current usage atoms")
    total_tokens = _cost_integer(report["total_tokens"], "cost_report.total_tokens")
    total_micro_usd = _cost_integer(
        report["total_micro_usd"], "cost_report.total_micro_usd",
    )
    total_api_list_micro_usd = _cost_integer(
        report["total_api_list_micro_usd"],
        "cost_report.total_api_list_micro_usd",
    )
    dimensions = _cost_dimensions(report["dimensions"], "cost_report.dimensions")
    dimension_tokens = sum(
        cast(Mapping[str, object], dimensions[dimension])["tokens"]
        for dimension in COST_REPORT_DIMENSIONS
    )
    dimension_cost = sum(
        cast(Mapping[str, object], dimensions[dimension])["micro_usd"]
        for dimension in COST_REPORT_DIMENSIONS
    )
    dimension_api_list_cost = sum(
        cast(Mapping[str, object], dimensions[dimension])["api_list_micro_usd"]
        for dimension in COST_REPORT_DIMENSIONS
    )
    if (dimension_tokens, dimension_cost, dimension_api_list_cost) != (
        total_tokens, total_micro_usd, total_api_list_micro_usd,
    ):
        raise ProtocolValidationError("cost_report totals disagree with dimensions")
    priced = _cost_coverage(report["priced"], "cost_report.priced")
    unpriced = _cost_coverage(report["unpriced"], "cost_report.unpriced")
    if priced["tokens"] + unpriced["tokens"] != total_tokens:
        raise ProtocolValidationError(
            "cost_report priced and unpriced tokens disagree with total"
        )
    _cost_rows(
        report["models"], "cost_report.models", COST_MODEL_FIELDS,
        MAX_COST_DETAIL_MODELS,
    )
    _cost_rows(
        report["agents"], "cost_report.agents", COST_AGENT_FIELDS,
        MAX_COST_DETAIL_AGENTS,
    )
    evidence = _cost_evidence(report["evidence"])
    catalog_revision = _cost_integer(
        report["catalog_revision"], "cost_report.catalog_revision",
    )
    expected_revision = max(
        (cast(int, row["catalog_revision"]) for row in evidence), default=0,
    )
    if catalog_revision < expected_revision:
        raise ProtocolValidationError(
            "cost_report.catalog_revision predates its pricing evidence"
        )
    omissions = _fields(report["omissions"], "cost_report.omissions", COST_OMISSION_FIELDS)
    for field in COST_OMISSION_FIELDS:
        _cost_integer(omissions[field], f"cost_report.omissions.{field}")
    return cast(dict[str, object], value)


def bucket_identity(value: Mapping[str, object]) -> BucketIdentity:
    """A full bucket replacement is keyed only by its epoch-aligned time cell."""
    return (
        "bucket",
        _integer(value.get("start"), "bucket.start"),
        _integer(value.get("duration"), "bucket.duration", minimum=1),
    )


def no_data_identity(value: Mapping[str, object]) -> NoDataIdentity:
    """A no-data replacement is keyed by its source epoch and exact bounded interval."""
    start = _number(value.get("start"), "no_data.start")
    end = _number(value.get("end"), "no_data.end")
    if end <= start:
        raise ProtocolValidationError("no_data identity must have end after start")
    return (
        "no_data",
        _identity_text(value.get("family"), "no_data.family"),
        _identity_text(
            value.get("source_id"), "no_data.source_id", maximum_bytes=MAX_SOURCE_ID_BYTES
        ),
        _identity_text(value.get("epoch"), "no_data.epoch"),
        start,
        end,
    )


def tombstone_identity(value: object, resolution_seconds: int) -> DeltaIdentity:
    if not isinstance(value, Mapping):
        raise ProtocolValidationError("tombstone must be an object")
    kind = value.get("kind")
    if kind == "bucket":
        item = _fields(value, "bucket tombstone", BUCKET_TOMBSTONE_FIELDS)
        identity = bucket_identity(item)
        if identity[2] != resolution_seconds or identity[1] % resolution_seconds:
            raise ProtocolValidationError(
                "bucket tombstone duration/alignment does not match resolution"
            )
        return identity
    if kind == "no_data":
        item = _fields(value, "no_data tombstone", NO_DATA_TOMBSTONE_FIELDS)
        return no_data_identity(item)
    raise ProtocolValidationError("tombstone.kind must be bucket or no_data")


def _tombstones(value: object, resolution_seconds: int) -> tuple[
    list[dict[str, object]], set[DeltaIdentity]
]:
    if not isinstance(value, list):
        raise ProtocolValidationError("tombstones must be an array")
    if len(value) > MAX_DELTA_IDENTITIES:
        raise ProtocolValidationError(
            f"tombstones must contain at most {MAX_DELTA_IDENTITIES} identities"
        )
    identities: set[DeltaIdentity] = set()
    previous: DeltaIdentity | None = None
    for index, item in enumerate(value):
        identity = tombstone_identity(item, resolution_seconds)
        if previous is not None and identity <= previous:
            raise ProtocolValidationError("tombstone identities must be unique and ordered")
        identities.add(identity)
        previous = identity
    return cast(list[dict[str, object]], value), identities


def _buckets(value: object, resolution_seconds: int, window: tuple[int, int] | None = None) -> list[dict[str, object]]:
    if not isinstance(value, list) or len(value) > resolution_policy.MAX_BUCKETS:
        raise ProtocolValidationError(f"buckets must be an array of at most {resolution_policy.MAX_BUCKETS}")
    previous = -1
    for index, item in enumerate(value):
        bucket = _fields(item, f"buckets[{index}]", BUCKET_FIELDS)
        start = _integer(bucket["start"], "bucket.start")
        duration = _integer(bucket["duration"], "bucket.duration", minimum=1)
        if duration != resolution_seconds or start % resolution_seconds:
            raise ProtocolValidationError("bucket duration/alignment does not match resolution")
        if start <= previous:
            raise ProtocolValidationError("bucket starts must be strictly increasing")
        if window and (start + duration <= window[0] or start >= window[1]):
            raise ProtocolValidationError("bucket lies outside the response window")
        series = bucket["series"]
        if not isinstance(series, Mapping) or any(not isinstance(name, str) for name in series):
            raise ProtocolValidationError("bucket.series must be an object")
        for name, raw_value in series.items():
            _identity_text(name, "bucket.series name", maximum_bytes=MAX_SOURCE_ID_BYTES)
            series_value = _fields(raw_value, f"bucket.series[{name!r}]", SERIES_VALUE_FIELDS)
            _number(series_value["value"], "series.value")
            _integer(series_value["source_count"], "series.source_count", minimum=1)
            first_series = _number(series_value["first_timestamp"], "series.first_timestamp")
            last_series = _number(series_value["last_timestamp"], "series.last_timestamp")
            if first_series > last_series or first_series < start or last_series >= start + duration:
                raise ProtocolValidationError("series timestamps are reversed or outside the bucket")
        source = _fields(bucket["source"], "bucket.source", SOURCE_FIELDS)
        count = _integer(source["count"], "source.count")
        first, last = source["first_timestamp"], source["last_timestamp"]
        if count == 0 and (first is not None or last is not None):
            raise ProtocolValidationError("empty source facts cannot have timestamps")
        if count and (first is None or last is None or _number(first, "source.first") > _number(last, "source.last")):
            raise ProtocolValidationError("non-empty source timestamps are missing or reversed")
        if bool(series) != bool(count):
            raise ProtocolValidationError("empty series requires empty source facts")
        if not isinstance(bucket["open"], bool) or (bucket["open"] and index != len(value) - 1):
            raise ProtocolValidationError("only the final bucket may be open")
        previous = start
    return cast(list[dict[str, object]], value)


def _no_data(
    value: object,
    window: tuple[int, int] | None = None,
    *,
    max_items: int | None = None,
) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise ProtocolValidationError("no_data must be an array")
    if max_items is not None and len(value) > max_items:
        raise ProtocolValidationError(f"no_data must contain at most {max_items} identities")
    previous: tuple[str, str, int | float, int | float] | None = None
    source_ends: dict[tuple[str, str], int | float] = {}
    for index, item in enumerate(value):
        span = _fields(item, f"no_data[{index}]", NO_DATA_FIELDS)
        family, source_id, reason = span["family"], span["source_id"], span["reason"]
        if any(not isinstance(value, str) or not value for value in (family, source_id, reason)):
            raise ProtocolValidationError("no_data family, source_id, and reason must be non-empty strings")
        if len(source_id.encode("utf-8")) > MAX_SOURCE_ID_BYTES:
            raise ProtocolValidationError("no_data.source_id is too long")
        start, end = _number(span["start"], "no_data.start"), _number(span["end"], "no_data.end")
        if not isinstance(span["epoch"], str) or not span["epoch"]:
            raise ProtocolValidationError("no_data.epoch must be a non-empty string")
        cadence = _number(span["source_cadence_seconds"], "no_data.source_cadence_seconds")
        if cadence <= 0:
            raise ProtocolValidationError("no_data.source_cadence_seconds must be positive")
        source_key = (family, source_id)
        key = (*source_key, start, end)
        if end <= start or (previous is not None and key <= previous) or start < source_ends.get(source_key, start):
            raise ProtocolValidationError("no_data spans are empty, overlapping, or unordered")
        if window and (start < window[0] or end > window[1]):
            raise ProtocolValidationError("no_data span lies outside the response window")
        previous, source_ends[source_key] = key, end
    return cast(list[dict[str, object]], value)


def validate_snapshot(value: object) -> SnapshotWire:
    data = _fields(value, "snapshot", SNAPSHOT_FIELDS)
    if data["protocol_version"] != WIRE_PROTOCOL_VERSION:
        raise ProtocolValidationError("unsupported snapshot protocol_version")
    range_seconds, _, concrete = _key(data["range_seconds"], data["requested_resolution"], data["resolution_seconds"])
    start, end = _integer(data["window_start"], "window_start"), _integer(data["window_end"], "window_end")
    if end - start != range_seconds:
        raise ProtocolValidationError("snapshot window length does not match range_seconds")
    _number(data["generated_at"], "generated_at")
    _generation(data["source_generation"], "source_generation")
    _generation(data["cache_generation"], "cache_generation")
    if not isinstance(data["rightmost_open"], bool):
        raise ProtocolValidationError("rightmost_open must be a boolean")
    buckets = _buckets(data["buckets"], concrete, (start, end))
    _no_data(data["no_data"], (start, end))
    validate_cost_report(data["cost_report"])
    if buckets and buckets[-1]["open"] != data["rightmost_open"]:
        raise ProtocolValidationError("rightmost_open disagrees with the final bucket")
    return cast(SnapshotWire, value)


def validate_delta(value: object) -> DeltaWire:
    data = _fields(value, "stats_delta", DELTA_FIELDS)
    if data["protocol_version"] != WIRE_PROTOCOL_VERSION:
        raise ProtocolValidationError("unsupported delta protocol_version")
    range_seconds = _integer(data["range_seconds"], "range_seconds", minimum=1)
    concrete = _integer(data["resolution_seconds"], "resolution_seconds", minimum=1)
    if not resolution_policy.is_supported(range_seconds, concrete):
        raise ProtocolValidationError("delta has an unsupported Range/Resolution key")
    _generation(data["source_generation"], "source_generation")
    base_generation = _generation(data["base_cache_generation"], "base_cache_generation")
    cache_generation = _generation(data["cache_generation"], "cache_generation")
    _generation(data["revision"], "revision", minimum=1)
    if cache_generation <= base_generation:
        raise ProtocolValidationError("delta cache_generation must advance beyond its base")
    buckets = _buckets(data["buckets"], concrete)
    spans = _no_data(data["no_data"], max_items=MAX_DELTA_IDENTITIES)
    tombstones, removed = _tombstones(data["tombstones"], concrete)
    validate_cost_report(data["cost_report"])
    if len(buckets) + len(spans) + len(tombstones) > MAX_DELTA_IDENTITIES:
        raise ProtocolValidationError(
            f"delta must contain at most {MAX_DELTA_IDENTITIES} identities"
        )
    replaced: set[DeltaIdentity] = {bucket_identity(item) for item in buckets}
    replaced.update(no_data_identity(item) for item in spans)
    if replaced & removed:
        raise ProtocolValidationError("a delta identity cannot be both replaced and removed")
    if not replaced and not removed:
        raise ProtocolValidationError("delta must contain a replacement")
    return cast(DeltaWire, value)


def pending_response(request: SnapshotRequest, retry_after_seconds: int, reason: str = "materialization is not ready") -> PendingWire:
    _key(request.range_seconds, request.resolution, request.resolution_seconds)
    retry = _integer(retry_after_seconds, "retry_after_seconds", minimum=1)
    if retry > MAX_RETRY_AFTER_SECONDS:
        raise ProtocolValidationError("retry_after_seconds exceeds the bounded maximum")
    if not isinstance(reason, str) or not reason:
        raise ProtocolValidationError("pending reason must be non-empty")
    return {"status": "pending", "protocol_version": WIRE_PROTOCOL_VERSION, "range_seconds": request.range_seconds, "requested_resolution": request.resolution, "resolution_seconds": request.resolution_seconds, "retry_after_seconds": retry, "reason": reason}


def upgrade_required_response(required_protocol_version: int, required_schema_generation: int, required_build: str, reason: str = "client or writer is too old") -> UpgradeRequiredWire:
    protocol_version = _integer(required_protocol_version, "required_protocol_version", minimum=1)
    schema_generation = _integer(required_schema_generation, "required_schema_generation", minimum=1)
    if not isinstance(required_build, str) or not required_build or not isinstance(reason, str) or not reason:
        raise ProtocolValidationError("required_build and reason must be non-empty")
    return {"status": "upgrade_required", "protocol_version": WIRE_PROTOCOL_VERSION, "required_protocol_version": protocol_version, "required_schema_generation": schema_generation, "required_build": required_build, "reason": reason}


def validate_snapshot_for_request(request: SnapshotRequest, response: SnapshotWire, minimum_generation: int | None = None) -> None:
    response = validate_snapshot(response)
    if (response["range_seconds"], response["requested_resolution"], response["resolution_seconds"]) != (request.range_seconds, request.resolution, request.resolution_seconds):
        raise ProtocolValidationError("snapshot key does not match the active request")
    floor = request.since_generation if minimum_generation is None else minimum_generation
    if floor is not None and response["cache_generation"] <= floor:
        raise ProtocolValidationError("snapshot cache generation is stale")


def validate_delta_for_snapshot(snapshot: SnapshotWire, delta: DeltaWire) -> None:
    snapshot, delta = validate_snapshot(snapshot), validate_delta(delta)
    if (delta["range_seconds"], delta["resolution_seconds"]) != (snapshot["range_seconds"], snapshot["resolution_seconds"]):
        raise ProtocolValidationError("delta key does not match the active snapshot")
    if delta["source_generation"] < snapshot["source_generation"]:
        raise ProtocolValidationError("delta source generation regressed")
    if delta["base_cache_generation"] != snapshot["cache_generation"]:
        raise ProtocolValidationError("delta base does not match the active snapshot")


def validate_delta_after_delta(previous: DeltaWire, delta: DeltaWire) -> None:
    """Require an unbroken cache predecessor and event revision; a gap needs a snapshot."""
    previous, delta = validate_delta(previous), validate_delta(delta)
    if (delta["range_seconds"], delta["resolution_seconds"]) != (
        previous["range_seconds"],
        previous["resolution_seconds"],
    ):
        raise ProtocolValidationError("delta key does not match the active delta stream")
    if delta["source_generation"] < previous["source_generation"]:
        raise ProtocolValidationError("delta source generation regressed")
    if delta["base_cache_generation"] != previous["cache_generation"]:
        raise ProtocolValidationError("delta base does not match the active cache generation")
    if delta["revision"] != previous["revision"] + 1:
        raise ProtocolValidationError("delta revision is not consecutive")


def live_cadence_seconds(resolution_seconds: int) -> int:
    """Derive cadence only from the server-echoed concrete resolution."""
    return resolution_policy.live_cadence_seconds(resolution_seconds)
