# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Focused tests for the current YO!stats family owner."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from yomux_lib.stats_current import families
from yomux_lib.stats_current.scheduler import COLLECTED_FAMILIES


def test_manifest_owns_every_current_family_cadence_and_fold():
    expected = {
        "cpu": ("cpu", 1, 1, families.FoldKind.AVERAGE, True),
        "agent_status": ("agent_status", 10, 60, families.FoldKind.STATUS, True),
        "gpu": ("gpu", 10, 60, families.FoldKind.GAUGE, True),
        "service_load": ("service_load", 1, 1, families.FoldKind.AVERAGE, True),
        "system_memory": ("system_memory", 60, 60, families.FoldKind.GAUGE, True),
        "system_disk": ("system_disk", None, None, families.FoldKind.GAUGE, False),
        "agent_tokens": ("agent_tokens", 10, 60, families.FoldKind.RATE, False),
        "cost": ("agent_tokens", 10, 60, families.FoldKind.USAGE, False),
        "browser": ("browser", None, None, families.FoldKind.RATE, True),
    }
    assert {
        family.name: (
            family.coverage_family,
            family.active_cadence_seconds,
            family.idle_cadence_seconds,
            family.fold_kind,
            family.no_data_eligible,
        )
        for family in families.CURRENT_FAMILIES
    } == expected
    assert families.FAMILY_BY_NAME["agent_tokens"].cadence_seconds(watched=True) == 10
    assert families.FAMILY_BY_NAME["agent_tokens"].cadence_seconds(watched=False) == 60
    for name in ("agent_status", "gpu"):
        assert families.FAMILY_BY_NAME[name].cadence_seconds(watched=True) == 10
        assert families.FAMILY_BY_NAME[name].cadence_seconds(watched=False) == 60
    assert families.FAMILY_BY_NAME["service_load"].cadence_seconds(watched=True) == 1
    assert families.FAMILY_BY_NAME["service_load"].cadence_seconds(watched=False) == 1
    assert families.FAMILY_BY_NAME["browser"].cadence_seconds(watched=True) is None
    assert families.FAMILY_BY_NAME["cost"].coverage_family == "agent_tokens"


def test_system_disk_is_readable_but_not_collected_by_the_deprecated_python_scheduler():
    assert "system_disk" in families.FAMILY_BY_NAME
    assert "system_disk" not in COLLECTED_FAMILIES


def test_every_server_final_series_has_one_owner():
    declared = [series for family in families.CURRENT_FAMILIES for series in family.series]
    assert len(declared) == len(set(declared))
    assert dict(families.SERIES_OWNER) == {
        series: family.name for family in families.CURRENT_FAMILIES for series in family.series
    }
    assert families.SERIES_OWNER["model_tokens_per_minute"] == "agent_tokens"
    assert families.SERIES_OWNER["browser_latency_ms"] == "browser"
    assert families.SERIES_OWNER["mac_pressure_level"] == "system_memory"
    assert "cpu_percent" not in families.SERIES_OWNER
    assert families.SERIES_OWNER["process_memory_bytes"] == "system_memory"
    assert families.SERIES_OWNER["process_cpu_percent_by_binary"] == "cpu"


@pytest.mark.parametrize(
    ("family_name", "payload"),
    [
        ("cpu", {"process_percent": 12.5, "system_percent": 42.0}),
        ("agent_status", {"states": {
            "agent-a": "ask", "agent-b": "run", "agent-c": "transition", "agent-d": "idle",
        }, "session_states": {"session-a": "ask", "session-b": "run"}}),
        ("agent_status", {"states": {}}),
        ("gpu", {
            "util_percent": 25.0, "memory_used_bytes": 1024,
            "memory_capacity_bytes": 4096, "label": "GPU 0",
        }),
        ("service_load", {"running": True, "cpu_percent": 2.0, "rss_bytes": 2048}),
        ("service_load", {"running": False, "cpu_percent": 0, "rss_bytes": None}),
        ("cpu", {"process_percent": 5, "system_percent": 20, "process_cpu_percent": {"python": 4}}),
        ("system_memory", {"used_bytes": 10, "capacity_bytes": 20}),
        ("system_disk", {"mountpoint": "/data", "used_percent": 25.0}),
        ("system_memory", {"used_bytes": 10, "capacity_bytes": 20, "process_memory_bytes": {"python": 8, "node": 4}}),
        ("system_memory", {"used_bytes": 10, "capacity_bytes": 20, "pressure_percent": 35}),
        ("system_memory", {"used_bytes": 10, "capacity_bytes": 20, "mac_pressure_percent": 35, "mac_pressure_level": 4}),
        ("browser", {"kind": "api", "latency_ms": 15, "bytes": 512}),
        ("browser", {"kind": "sse", "bytes": 256}),
        ("browser", {"kind": "heartbeat"}),
        ("browser", {"kind": "disconnect", "duration_ms": 5000}),
    ],
)
def test_present_collector_payload_envelopes_validate(family_name, payload):
    validated = families.validate_payload(family_name, payload)
    assert dict(validated) == payload
    with pytest.raises(TypeError):
        validated["unexpected"] = 1


@pytest.mark.parametrize(
    ("family_name", "payload", "message"),
    [
        ("missing", {}, "unknown current stats family"),
        ("cpu", {}, "missing fields"),
        ("cpu", {"process_percent": -1, "system_percent": 1}, "non-negative finite"),
        ("system_disk", {"mountpoint": "/data", "used_percent": 101}, "between 0 and 100"),
        ("system_disk", {"mountpoint": "", "used_percent": 50}, "non-empty string"),
        ("agent_status", {"states": {"agent-a": "working"}}, "must be one of"),
        ("agent_status", {"states": {"": "idle"}}, "agent ids must be non-empty"),
        ("gpu", {
            "util_percent": 1, "memory_used_bytes": 2, "memory_capacity_bytes": 3,
            "label": "",
        }, "non-empty string"),
        ("service_load", {"running": 1, "cpu_percent": 0, "rss_bytes": None}, "boolean"),
        ("agent_tokens", {}, "usage-derived"),
        ("cost", {}, "usage-derived"),
        ("browser", {}, "missing fields"),
        ("browser", {"kind": "api", "latency_ms": float("nan")}, "non-negative finite"),
        ("browser", {"kind": "poll"}, "must be one of"),
        ("browser", {"kind": "api", "duration_ms": 10}, "does not accept duration_ms"),
        ("browser", {"kind": "disconnect"}, "requires only duration_ms"),
        ("browser", {"kind": "disconnect", "duration_ms": 10, "bytes": 1}, "requires only duration_ms"),
        ("browser", {"kind": "api", "start": 100}, "unknown fields"),
    ],
)
def test_invalid_payloads_fail_at_the_family_boundary(family_name, payload, message):
    with pytest.raises(families.FamilyValidationError, match=message):
        families.validate_payload(family_name, payload)


@pytest.mark.parametrize("extra", [{}, {"observed_at": 0}, {"observed_at": 123.5}])
def test_agent_roster_preserves_optional_observation_time(extra):
    row = {"id": "agent", "kind": "codex", "state": "run", "session": "work", "pane": "%1", **extra}
    payload = {"states": {"agent": "run"}, "agents": [row]}
    assert families.validate_payload("agent_status", payload)["agents"] == [row]


@pytest.mark.parametrize("extra", [
    {"observed_at": -1}, {"observed_at": float("nan")}, {"observed_at": float("inf")},
    {"observed_at": True}, {"observed_at": "123"}, {"observed_at": None},
    {"timestamp": 123}, {"observed_at": 123, "timestamp": 123},
])
def test_agent_roster_rejects_invalid_observation_time_and_unknown_fields(extra):
    row = {"id": "agent", "kind": "codex", "state": "run", "session": "work", "pane": "%1", **extra}
    with pytest.raises(families.FamilyValidationError):
        families.validate_payload("agent_status", {"states": {"agent": "run"}, "agents": [row]})


def test_current_family_owner_has_no_old_pipeline_dependencies_or_concepts():
    source = Path(families.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert not imported & {
        "yomux_lib.stats_families",
        "yomux_lib.statsd",
        "yomux_lib.local_services.stats_store",
    }
    assert len(source.splitlines()) < 300
    for retired_concept in (
        "legacy_aliases", "retention_tiers", "token_stream", "wire_group",
        "compatibility_flag", "chart_groups", "cpu_total_percent", "agent_activity_samples",
        "host_metrics", "api_count", "latency_total_ms", '_field("bandwidth_bytes"',
    ):
        assert retired_concept not in source
