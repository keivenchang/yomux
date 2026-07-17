# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Pure builders from successful native samples to current YO!stats facts."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Callable
from typing import Iterable
from typing import Mapping

from .families import FAMILY_BY_NAME
from .families import validate_payload
from .storage import CoverageEpoch
from .storage import Observation
from .storage import UsageAtom
from .storage import UsageAtomTombstone
from .usage import normalize_usage_atom


MAX_ID_BYTES = 256


@dataclass(frozen=True, slots=True)
class CollectorReceipt:
    commit: Callable[[], None]
    rollback: Callable[[], None]


@dataclass(frozen=True, slots=True)
class CollectorFacts:
    observations: tuple[Observation, ...] = ()
    coverage_epochs: tuple[CoverageEpoch, ...] = ()
    usage_atoms: tuple[UsageAtom, ...] = ()
    usage_tombstones: tuple[UsageAtomTombstone, ...] = ()
    receipt: CollectorReceipt | None = None
    budget_exhausted_follow_up: bool = False


@dataclass(frozen=True, slots=True)
class GpuDeviceSample:
    source_id: str
    util_percent: float
    memory_used_bytes: float
    memory_capacity_bytes: float
    label: str


@dataclass(frozen=True, slots=True)
class ServiceLoadSample:
    source_id: str
    running: bool
    cpu_percent: float
    rss_bytes: float | None


def cpu_success(
    *, epoch_id: str, epoch_started_at: float, observed_at: float,
    cadence_seconds: float, owner_generation: int, source_id: str,
    process_percent: float, system_percent: float,
) -> CollectorFacts:
    return _single(
        "cpu", source_id, {"process_percent": process_percent, "system_percent": system_percent},
        epoch_id, epoch_started_at, observed_at, cadence_seconds, owner_generation,
    )


def agent_status_success(
    *, epoch_id: str, epoch_started_at: float, observed_at: float,
    cadence_seconds: float, owner_generation: int, source_id: str,
    states: Mapping[str, str],
) -> CollectorFacts:
    return _single(
        "agent_status", source_id, {"states": dict(states)}, epoch_id,
        epoch_started_at, observed_at, cadence_seconds, owner_generation,
    )


def gpu_devices_success(
    devices: Iterable[GpuDeviceSample],
    *, epoch_id: str, epoch_started_at: float, observed_at: float,
    cadence_seconds: float, owner_generation: int,
) -> CollectorFacts:
    rows = (
        (device.source_id, {
            "util_percent": device.util_percent,
            "memory_used_bytes": device.memory_used_bytes,
            "memory_capacity_bytes": device.memory_capacity_bytes,
            "label": device.label,
        })
        for device in devices
    )
    return _many(
        rows, family="gpu", epoch_id=epoch_id, epoch_started_at=epoch_started_at,
        observed_at=observed_at, cadence_seconds=cadence_seconds,
        owner_generation=owner_generation,
    )


def service_load_success(
    services: Iterable[ServiceLoadSample],
    *, epoch_id: str, epoch_started_at: float, observed_at: float,
    cadence_seconds: float, owner_generation: int,
) -> CollectorFacts:
    rows = (
        (service.source_id, {
            "running": service.running,
            "cpu_percent": service.cpu_percent,
            "rss_bytes": service.rss_bytes,
        })
        for service in services
    )
    return _many(
        rows, family="service_load", epoch_id=epoch_id, epoch_started_at=epoch_started_at,
        observed_at=observed_at, cadence_seconds=cadence_seconds,
        owner_generation=owner_generation,
    )


def system_memory_success(
    *, epoch_id: str, epoch_started_at: float, observed_at: float,
    cadence_seconds: float, owner_generation: int, source_id: str,
    used_bytes: float, capacity_bytes: float,
) -> CollectorFacts:
    return _single(
        "system_memory", source_id, {"used_bytes": used_bytes, "capacity_bytes": capacity_bytes},
        epoch_id, epoch_started_at, observed_at, cadence_seconds, owner_generation,
    )


def browser_event_success(
    *, epoch_id: str, epoch_started_at: float, observed_at: float,
    cadence_seconds: float, owner_generation: int, source_id: str,
    event_key: str, kind: str, latency_ms: float | None = None,
    bytes_count: float | None = None, duration_ms: float | None = None,
) -> CollectorFacts:
    payload: dict[str, object] = {"kind": kind}
    if latency_ms is not None:
        payload["latency_ms"] = latency_ms
    if bytes_count is not None:
        payload["bytes"] = bytes_count
    if duration_ms is not None:
        payload["duration_ms"] = duration_ms
    return _single(
        "browser", source_id, payload, epoch_id, epoch_started_at, observed_at,
        cadence_seconds, owner_generation, event_key=event_key,
    )


def usage_scan_success(
    atoms: Iterable[UsageAtom],
    tombstones: Iterable[UsageAtomTombstone] = (),
    receipt: CollectorReceipt | None = None,
    *, epoch_id: str, epoch_started_at: float, observed_at: float,
    cadence_seconds: float, owner_generation: int, source_id: str,
    budget_exhausted_follow_up: bool = False,
) -> CollectorFacts:
    if not isinstance(budget_exhausted_follow_up, bool):
        raise ValueError("budget_exhausted_follow_up must be boolean")
    if budget_exhausted_follow_up and receipt is None:
        raise ValueError("budget_exhausted_follow_up requires a receipt")
    coverage = _coverage(
        "agent_tokens", source_id, epoch_id, epoch_started_at, observed_at,
        cadence_seconds, owner_generation,
    )
    return CollectorFacts(
        coverage_epochs=(coverage,),
        usage_atoms=tuple(normalize_usage_atom(atom) for atom in atoms),
        usage_tombstones=tuple(tombstones),
        receipt=receipt,
        budget_exhausted_follow_up=budget_exhausted_follow_up,
    )


def close_epoch(
    *, family: str, source_id: str, epoch_id: str, epoch_started_at: float,
    ended_at: float, cadence_seconds: float, owner_generation: int,
) -> CollectorFacts:
    if family not in FAMILY_BY_NAME:
        raise ValueError(f"unknown current family {family!r}")
    coverage_family = FAMILY_BY_NAME[family].coverage_family
    _validate_native_cadence(coverage_family, cadence_seconds)
    started, ended, cadence, generation = _context(
        epoch_id, epoch_started_at, ended_at, cadence_seconds, owner_generation,
    )
    return CollectorFacts(coverage_epochs=(CoverageEpoch(
        coverage_family, _text(source_id, "source_id"), _text(epoch_id, "epoch_id"),
        started, ended, cadence, generation,
    ),))


def _single(
    family: str, source_id: str, payload: Mapping[str, object], epoch_id: str,
    epoch_started_at: float, observed_at: float, cadence_seconds: float,
    owner_generation: int, *, event_key: str = "sample",
) -> CollectorFacts:
    source = _text(source_id, "source_id")
    epoch = _text(epoch_id, "epoch_id")
    started, observed, cadence, generation = _context(
        epoch, epoch_started_at, observed_at, cadence_seconds, owner_generation,
    )
    normalized = validate_payload(family, payload)
    observation = Observation(
        _event_id(family, source, epoch, observed, event_key), family, source,
        observed, epoch, generation, normalized,
    )
    coverage = _coverage(family, source, epoch, started, observed, cadence, generation)
    return CollectorFacts((observation,), (coverage,))


def _many(
    rows: Iterable[tuple[str, Mapping[str, object]]], *, family: str, epoch_id: str,
    epoch_started_at: float, observed_at: float, cadence_seconds: float,
    owner_generation: int,
) -> CollectorFacts:
    _validate_native_cadence(family, cadence_seconds)
    _context(epoch_id, epoch_started_at, observed_at, cadence_seconds, owner_generation)
    facts = tuple(
        _single(
            family, source_id, payload, epoch_id, epoch_started_at, observed_at,
            cadence_seconds, owner_generation,
        )
        for source_id, payload in rows
    )
    return CollectorFacts(
        tuple(item for fact in facts for item in fact.observations),
        tuple(item for fact in facts for item in fact.coverage_epochs),
    )


def _coverage(
    family: str, source_id: str, epoch_id: str, epoch_started_at: float,
    observed_at: float, cadence_seconds: float, owner_generation: int,
) -> CoverageEpoch:
    _validate_native_cadence(family, cadence_seconds)
    started, observed, cadence, generation = _context(
        epoch_id, epoch_started_at, observed_at, cadence_seconds, owner_generation,
    )
    return CoverageEpoch(
        FAMILY_BY_NAME[family].coverage_family,
        _text(source_id, "source_id"), _text(epoch_id, "epoch_id"),
        started, observed + cadence, cadence, generation,
    )


def _validate_native_cadence(family: str, cadence_seconds: object) -> None:
    spec = FAMILY_BY_NAME[family]
    allowed = {
        float(value)
        for value in (spec.active_cadence_seconds, spec.idle_cadence_seconds)
        if value is not None
    }
    if allowed and cadence_seconds not in allowed:
        choices = ", ".join(f"{value:g}" for value in sorted(allowed))
        raise ValueError(f"{family} cadence_seconds must be one of: {choices}")


def _context(
    epoch_id: str, epoch_started_at: float, observed_at: float,
    cadence_seconds: float, owner_generation: int,
) -> tuple[float, float, float, int]:
    _text(epoch_id, "epoch_id")
    started = _number(epoch_started_at, "epoch_started_at")
    observed = _number(observed_at, "observed_at")
    cadence = _number(cadence_seconds, "cadence_seconds")
    if cadence <= 0 or observed < started or not math.isfinite(observed + cadence):
        raise ValueError("collector epoch, timestamp, or cadence is invalid")
    if isinstance(owner_generation, bool) or not isinstance(owner_generation, int) or owner_generation < 0:
        raise ValueError("owner_generation must be a non-negative integer")
    return started, observed, cadence, owner_generation


def _event_id(family: str, source_id: str, epoch_id: str, observed_at: float, event_key: str) -> str:
    parts = (family, source_id, epoch_id, observed_at.hex(), _text(event_key, "event_key"))
    encoded = "".join(f"{len(part)}:{part}" for part in parts).encode("utf-8")
    return f"obs-{family}-{hashlib.sha256(encoded).hexdigest()}"


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    normalized = value.strip()
    if len(normalized.encode("utf-8")) > MAX_ID_BYTES or any(ord(char) < 32 for char in normalized):
        raise ValueError(f"{name} is too long or contains controls")
    return normalized


def _number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite non-negative number")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized < 0:
        raise ValueError(f"{name} must be a finite non-negative number")
    return normalized
