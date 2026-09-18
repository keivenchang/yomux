# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Shared value-kind constants for current stats family validation."""

NUMBER = "number"
NULLABLE_NUMBER = "nullable_number"
STRING = "string"
BOOLEAN = "boolean"
AGENT_STATES = "agent_states"
AGENT_STATE_VALUES = frozenset({"ask", "run", "transition", "idle"})
AGENT_COUNTS = "agent_counts"
AGENT_ROSTER = "agent_roster"
AGENT_ROSTER_STATE_VALUES = AGENT_STATE_VALUES | {"error", "unavailable"}
PROCESS_MEMORY_BYTES, PROCESS_CPU_PERCENT = "process_memory_bytes", "process_cpu_percent"
