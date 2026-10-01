# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Validation helpers for typed current stats payload values."""

from __future__ import annotations

from collections.abc import Mapping
import math

from .families_constants import (
    AGENT_COUNTS,
    AGENT_ROSTER,
    AGENT_ROSTER_STATE_VALUES,
    AGENT_STATES,
    AGENT_STATE_VALUES,
    NUMBER,
    NULLABLE_NUMBER,
    PROCESS_CPU_PERCENT,
    PROCESS_MEMORY_BYTES,
    STRING,
    BOOLEAN,
)
from .process_memory import MAX_PROCESS_BINARY_LENGTH, MAX_PROCESS_CPU_SERIES, MAX_PROCESS_MEMORY_PAYLOAD_SERIES, normalize_process_binary


def validate_value(value: object, kind: str, path: str, *, error_type: type[ValueError]) -> None:
    """Validate one payload value against its bounded family kind."""

    def fail(message: str) -> None:
        raise error_type(message)

    if kind in {NUMBER, NULLABLE_NUMBER}:
        if value is None and kind == NULLABLE_NUMBER:
            return
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            fail(f"{path} must be a non-negative finite number")
        if not math.isfinite(value) or value < 0:
            fail(f"{path} must be a non-negative finite number")
        return
    if kind == STRING:
        if not isinstance(value, str) or not value.strip():
            fail(f"{path} must be a non-empty string")
        return
    if kind == BOOLEAN:
        if not isinstance(value, bool):
            fail(f"{path} must be a boolean")
        return
    if kind == AGENT_STATES:
        if not isinstance(value, Mapping):
            fail(f"{path} must be an object")
        for source_id, state in value.items():
            if not isinstance(source_id, str) or not source_id.strip():
                fail(f"{path} agent ids must be non-empty strings")
            if state not in AGENT_STATE_VALUES:
                fail(f"{path}.{source_id} must be one of: {', '.join(sorted(AGENT_STATE_VALUES))}")
        return
    if kind == AGENT_COUNTS:
        if not isinstance(value, Mapping):
            fail(f"{path} must be an object")
        expected = {"total", "running", "red", "yellow"}
        if set(value) != expected:
            fail(f"{path} must contain exactly: {', '.join(sorted(expected))}")
        for name, count in value.items():
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                fail(f"{path}.{name} must be a non-negative integer")
        return
    if kind == AGENT_ROSTER:
        if not isinstance(value, list) or len(value) > 256:
            fail(f"{path} must be a bounded list")
        required = {"id", "kind", "state", "session", "pane"}
        for index, row in enumerate(value):
            if not isinstance(row, Mapping) or set(row) not in (required, required | {"observed_at"}):
                fail(f"{path}[{index}] must contain the bounded agent identity fields")
            if any(not isinstance(row[name], str) for name in required) or row["state"] not in AGENT_ROSTER_STATE_VALUES:
                fail(f"{path}[{index}] contains an invalid agent identity")
            if "observed_at" in row:
                validate_value(row["observed_at"], NUMBER, f"{path}[{index}].observed_at", error_type=error_type)
        return
    if kind in {PROCESS_MEMORY_BYTES, PROCESS_CPU_PERCENT}:
        if not isinstance(value, Mapping):
            fail(f"{path} must be an object")
        maximum = MAX_PROCESS_CPU_SERIES if kind == PROCESS_CPU_PERCENT else MAX_PROCESS_MEMORY_PAYLOAD_SERIES
        if len(value) > maximum:
            fail(f"{path} must contain at most {maximum} binaries")
        for binary, metric_value in value.items():
            if not isinstance(binary, str) or not binary or len(binary) > MAX_PROCESS_BINARY_LENGTH or normalize_process_binary(binary) != binary:
                fail(f"{path} binary keys must be normalized")
            validate_value(metric_value, NUMBER, f"{path}.{binary}", error_type=error_type)
            if kind == PROCESS_CPU_PERCENT and float(metric_value) > 100:
                fail(f"{path}.{binary} must be at most 100")
        return
    raise RuntimeError(f"unknown payload value kind {kind!r}")
