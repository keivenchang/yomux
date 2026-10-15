# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Declarative owners for current YO!stats observation families."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import TypeAlias

from ..polling_policy import quiet_poll_interval
from . import browser_family
from .family_values import validate_value
from .families_constants import (
    AGENT_COUNTS, AGENT_ROSTER, AGENT_ROSTER_STATE_VALUES, AGENT_STATE_VALUES, AGENT_STATES,
    BOOLEAN, NULLABLE_NUMBER, NUMBER, PROCESS_CPU_PERCENT, PROCESS_MEMORY_BYTES, STRING,
)


class FamilyValidationError(ValueError):
    """An observation does not match its current family contract."""


class FoldKind(StrEnum):
    """The materializer operation applied to original observations."""

    GAUGE = "gauge"
    AVERAGE = "average"
    RATE = "rate"
    STATUS = "status"
    USAGE = "usage"


ValueKind: TypeAlias = str

@dataclass(frozen=True, slots=True)
class PayloadField:
    """One bounded top-level fact in a collector payload."""

    name: str
    kind: ValueKind
    required: bool = True


@dataclass(frozen=True, slots=True)
class FamilySpec:
    """The complete current contract for one independently collected family."""

    name: str
    coverage_family: str
    active_cadence_seconds: float | None
    idle_cadence_seconds: float | None
    fold_kind: FoldKind
    payload_fields: tuple[PayloadField, ...] | None
    series: tuple[str, ...]
    no_data_eligible: bool

    def cadence_seconds(self, *, watched: bool) -> float | None:
        """Return the real collection cadence; ``None`` means event-driven."""

        if self.active_cadence_seconds is None or self.idle_cadence_seconds is None:
            return None
        return quiet_poll_interval(
            self.active_cadence_seconds,
            self.idle_cadence_seconds,
            0.0 if watched else 1.0,
        )

    def validate_payload(self, payload: object) -> Mapping[str, object]:
        """Validate one original payload without aggregating or rewriting it."""

        if self.payload_fields is None:
            raise FamilyValidationError(
                f"{self.name} is usage-derived and accepts no observation payload"
            )
        if not isinstance(payload, Mapping):
            raise FamilyValidationError(f"{self.name} payload must be an object")
        if any(not isinstance(key, str) for key in payload):
            raise FamilyValidationError(f"{self.name} payload fields must be strings")
        fields = {field.name: field for field in self.payload_fields}
        unknown = set(payload) - set(fields)
        if unknown:
            raise FamilyValidationError(
                f"{self.name} payload has unknown fields: {', '.join(sorted(unknown))}"
            )
        missing = {field.name for field in self.payload_fields if field.required} - set(payload)
        if missing:
            raise FamilyValidationError(
                f"{self.name} payload is missing fields: {', '.join(sorted(missing))}"
            )
        for name, value in payload.items():
            validate_value(value, fields[name].kind, f"{self.name}.{name}", error_type=FamilyValidationError)
        if self.name == "browser":
            try:
                browser_family.validate_payload(payload)
            except browser_family.BrowserPayloadError as error:
                raise FamilyValidationError(str(error)) from error
        if self.name == "system_disk":
            mountpoint = payload.get("mountpoint")
            used_percent = payload.get("used_percent")
            if not isinstance(mountpoint, str) or not mountpoint.strip() or len(mountpoint) > 4096:
                raise FamilyValidationError("system_disk.mountpoint must be a non-empty path")
            if not isinstance(used_percent, (int, float)) or not 0 <= used_percent <= 100:
                raise FamilyValidationError("system_disk.used_percent must be between 0 and 100")
        return MappingProxyType(dict(payload))


def _field(name: str, kind: ValueKind, *, required: bool = True) -> PayloadField:
    return PayloadField(name, kind, required)




CURRENT_FAMILIES = (
    FamilySpec(
        "cpu", "cpu", 1, 1, FoldKind.AVERAGE,
        (_field("process_percent", NUMBER), _field("system_percent", NUMBER),
         _field("process_cpu_percent", PROCESS_CPU_PERCENT, required=False),
         _field("process_census_incomplete", BOOLEAN, required=False)),
        (
            "system_cpu_percent", "system_cpu_min_percent", "system_cpu_max_percent",
            "process_cpu_percent", "process_cpu_min_percent", "process_cpu_max_percent", "process_cpu_percent_by_binary",
            "process_census_incomplete:cpu",
        ), True,
    ),
    FamilySpec(
        "agent_status", "agent_status", 10, 60, FoldKind.STATUS,
        (_field("states", AGENT_STATES), _field("session_states", AGENT_STATES, required=False), _field("snapshot_revision", NUMBER, required=False), _field("counts", AGENT_COUNTS, required=False), _field("agents", AGENT_ROSTER, required=False)),
        ("ask_agents", "run_agents", "transition_agents", "idle_agents", "ask_sessions", "run_sessions", "transition_sessions", "idle_sessions", "agent_window_snapshot_revision"), True,
    ),
    FamilySpec(
        "gpu", "gpu", 10, 60, FoldKind.GAUGE,
        (
            _field("util_percent", NUMBER), _field("memory_used_bytes", NUMBER),
            _field("memory_capacity_bytes", NUMBER), _field("label", STRING),
        ),
        ("gpu_util_percent", "gpu_memory_bytes"), True,
    ),
    FamilySpec(
        # Every 10-second display bucket needs multiple real process samples, including history
        # collected before a browser opens; otherwise its minimum, average, and maximum collapse.
        "service_load", "service_load", 1, 1, FoldKind.AVERAGE,
        (
            _field("running", BOOLEAN), _field("cpu_percent", NUMBER),
            _field("rss_bytes", NULLABLE_NUMBER),
        ),
        (
            "service_cpu_percent", "service_cpu_min_percent",
            "service_cpu_max_percent", "service_rss_bytes",
        ), True,
    ),
    FamilySpec(
        "system_memory", "system_memory", 60, 60, FoldKind.GAUGE,
        (
            _field("used_bytes", NUMBER), _field("capacity_bytes", NUMBER),
            _field("mac_physical_memory_bytes", NUMBER, required=False), _field("mac_memory_used_bytes", NUMBER, required=False),
            _field("mac_cached_files_bytes", NUMBER, required=False), _field("mac_swap_used_bytes", NUMBER, required=False),
            _field("mac_app_memory_bytes", NUMBER, required=False), _field("mac_wired_memory_bytes", NUMBER, required=False),
            _field("mac_compressed_memory_bytes", NUMBER, required=False), _field("mac_pressure_percent", NUMBER, required=False),
            _field("mac_pressure_level", NUMBER, required=False),
            _field("process_memory_bytes", PROCESS_MEMORY_BYTES, required=False),
            _field("process_census_incomplete", BOOLEAN, required=False),
            # A reverted pre-v6 Mac experiment persisted this key. Keep it
            # readable so one retired observation cannot block statsd startup;
            # the materializer deliberately does not publish it.
            _field("pressure_percent", NUMBER, required=False),
        ),
        (
            "system_memory_used_bytes", "system_memory_capacity_bytes", "mac_physical_memory_bytes", "mac_memory_used_bytes",
            "mac_cached_files_bytes", "mac_swap_used_bytes", "mac_app_memory_bytes", "mac_wired_memory_bytes",
            "mac_compressed_memory_bytes", "mac_pressure_percent", "mac_pressure_level",
            "process_memory_bytes", "process_census_incomplete:system_memory",
        ), True,
    ),
    FamilySpec(
        # Go owns collection. None keeps the deprecated Python scheduler from
        # creating a collector job while preserving the shared read contract.
        # It also avoids inventing Python outage spans for an unowned family.
        "system_disk", "system_disk", None, None, FoldKind.GAUGE,
        (_field("mountpoint", STRING), _field("used_percent", NUMBER)),
        ("system_disk_used_percent",), False,
    ),
    # Usage atoms are event-derived. The transcript scan cadence controls when
    # additional atoms are discovered; it is not evidence that an atom-free
    # sub-cadence bucket is a telemetry outage.
    FamilySpec(
        "agent_tokens", "agent_tokens", 10, 60, FoldKind.RATE,
        None,
        ("agent_tokens_per_minute", "model_tokens_per_minute"), False,
    ),
    FamilySpec(
        "cost", "agent_tokens", 10, 60, FoldKind.USAGE,
        None,
        ("cost_micro_usd", "api_list_cost_micro_usd", "usage_tokens"), False,
    ),
    FamilySpec(
        "browser", "browser", None, None, FoldKind.RATE,
        (
            _field("kind", STRING), _field("latency_ms", NUMBER, required=False),
            _field("bytes", NUMBER, required=False), _field("duration_ms", NUMBER, required=False),
            _field("endpoint", STRING, required=False), _field("method", STRING, required=False),
            _field("request_id", STRING, required=False), _field("status", NUMBER, required=False),
            _field("journey_id", STRING, required=False),
            _field("code_revision", STRING, required=False),
            _field("browser_family", STRING, required=False),
            _field("connection_protocol", STRING, required=False),
            _field("max_concurrency", NUMBER, required=False),
            _field("entry_count", NUMBER, required=False),
            _field("input_delay_ms", NUMBER, required=False),
            _field("processing_ms", NUMBER, required=False),
            _field("presentation_delay_ms", NUMBER, required=False),
            _field("interaction_type", STRING, required=False),
            _field("operation_kind", STRING, required=False),
            _field("outcome", STRING, required=False),
            *(_field(name, STRING, required=False) for name in ("signature", "message", "stack", "source", "provenance")),
            *(_field(name, STRING, required=False) for name in ("route", "event_type", "wall_time", "delivery_outcome")),
            _field("line", NUMBER, required=False),
            _field("column", NUMBER, required=False),
            *(_field(name, NUMBER, required=False) for name in sorted(browser_family.UPLOAD_HEALTH_FIELDS)),
            *(_field(name, NUMBER, required=False) for name in sorted(browser_family.API_PHASE_FIELDS)),
            *(_field(name, NUMBER, required=False) for name in sorted(browser_family.PAGE_PHASE_FIELDS)),
            _field("fanout_count", NUMBER, required=False),
        ),
        (
            "browser_api_per_second", "browser_sse_per_second", "browser_latency_ms",
            "browser_bandwidth_bytes_per_second", "browser_disconnected_ms", "browser_queue_ms",
            "browser_first_paint_ms", "browser_first_contentful_paint_ms",
            "browser_app_ready_ms", "browser_page_max_concurrency",
            "browser_finder_usable_ms", "browser_input_latency_ms",
            "browser_operation_wait_ms", "browser_long_task_ms",
            "browser_upload_queue_depth", "browser_upload_drops", "browser_upload_retries",
            "browser_instrumentation_cost_ms",
        ),
        True,
    ),
)

FAMILY_BY_NAME = MappingProxyType({family.name: family for family in CURRENT_FAMILIES})
SERIES_OWNER = MappingProxyType(
    {series: family.name for family in CURRENT_FAMILIES for series in family.series}
)

if len(FAMILY_BY_NAME) != len(CURRENT_FAMILIES):
    raise RuntimeError("current stats family names must be unique")
if len(SERIES_OWNER) != sum(len(family.series) for family in CURRENT_FAMILIES):
    raise RuntimeError("current stats series must have exactly one family owner")
for _family_spec in CURRENT_FAMILIES:
    if _family_spec.coverage_family not in FAMILY_BY_NAME:
        raise RuntimeError(f"unknown coverage owner for current stats family {_family_spec.name!r}")
    active = _family_spec.active_cadence_seconds
    idle = _family_spec.idle_cadence_seconds
    if (active is None) != (idle is None) or (active is not None and (active <= 0 or idle < active)):
        raise RuntimeError(f"invalid cadence for current stats family {_family_spec.name!r}")


def validate_payload(family_name: str, payload: object) -> Mapping[str, object]:
    """Validate an observation against its sole current family owner."""

    family = FAMILY_BY_NAME.get(family_name)
    if family is None:
        raise FamilyValidationError(f"unknown current stats family {family_name!r}")
    return family.validate_payload(payload)
