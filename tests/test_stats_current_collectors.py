# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Focused tests for pure current collector normalization."""

from types import MappingProxyType

import pytest

from yomux_lib.stats_current import collectors
from yomux_lib.stats_current.storage import DATABASE_FILENAME
from yomux_lib.stats_current.families import validate_payload
from yomux_lib.stats_current.storage import Store
from yomux_lib.stats_current.storage import UsageAtom

# Deprecated Python backend test. Disabled by default. Retained only for explicit maintenance until the Python backend is removed.
pytestmark = pytest.mark.deprecated_python_server


BASE = {
    "epoch_id": "boot-1",
    "epoch_started_at": 100.0,
    "owner_generation": 7,
}


def _usage(event_id="usage-1"):
    return UsageAtom(
        event_id, "input", "text", "read", "tokens", 108.0,
        {
            "quantity": 12,
            "provider": "openai",
            "model": "gpt-current",
            "agent_id": "agent-a",
            "telemetry_complete": True,
        },
    )


def test_builders_emit_only_normalized_current_family_facts():
    batches = (
        collectors.cpu_success(
            **BASE, observed_at=101, cadence_seconds=1, source_id="process:web",
            process_percent=5, system_percent=20,
            process_cpu_percent={"python": 4, "node": 3},
        ),
        collectors.agent_status_success(
            **BASE, observed_at=110, cadence_seconds=10, source_id="agent-scan",
            states={"agent-a": "run", "agent-b": "idle"},
        ),
        collectors.gpu_devices_success(
            (collectors.GpuDeviceSample("gpu:0", 25, 100, 1000, "GPU 0"),),
            **BASE, observed_at=110, cadence_seconds=10,
        ),
        collectors.service_load_success(
            (collectors.ServiceLoadSample("service:statsd", True, 4, 400),),
            **BASE, observed_at=110, cadence_seconds=1,
        ),
        collectors.system_memory_success(
            **BASE, observed_at=160, cadence_seconds=60, source_id="host",
            used_bytes=100, capacity_bytes=1000,
        ),
        collectors.browser_event_success(
            **BASE, observed_at=105, cadence_seconds=5, source_id="client:hashed",
            event_key="request-1", kind="api", latency_ms=12, bytes_count=80,
        ),
    )
    expected = ("cpu", "agent_status", "gpu", "service_load", "system_memory", "browser")
    assert tuple(batch.observations[0].family for batch in batches) == expected
    for batch in batches:
        assert len(batch.observations) == len(batch.coverage_epochs) == 1
        observation = batch.observations[0]
        assert dict(validate_payload(observation.family, observation.payload)) == dict(observation.payload)
        assert isinstance(observation.payload, MappingProxyType)
        assert not ({"bucket", "total", "samples", "history"} & set(observation.payload))


def test_independent_families_keep_their_native_timestamps_and_cadences():
    cpu = collectors.cpu_success(
        **BASE, observed_at=101, cadence_seconds=1, source_id="web",
        process_percent=1, system_percent=2,
    )
    gpu = collectors.gpu_devices_success(
        (collectors.GpuDeviceSample("gpu:0", 3, 4, 5, "GPU 0"),),
        **BASE, observed_at=109, cadence_seconds=10,
    )
    memory = collectors.system_memory_success(
        **BASE, observed_at=160, cadence_seconds=60, source_id="host",
        used_bytes=6, capacity_bytes=7,
        macos_details={"physical_memory_bytes": 7, "pressure_percent": 20, "pressure_level": 2},
    )
    assert [batch.observations[0].observed_at for batch in (cpu, gpu, memory)] == [101, 109, 160]
    assert [batch.coverage_epochs[0].native_cadence_seconds for batch in (cpu, gpu, memory)] == [1, 10, 60]
    assert memory.observations[0].payload["mac_pressure_percent"] == 20
    assert memory.observations[0].payload["mac_pressure_level"] == 2
    assert [batch.coverage_epochs[0].ended_at for batch in (cpu, gpu, memory)] == [102, 119, 220]


def test_system_memory_without_macos_details_keeps_the_linux_payload_exactly_legacy_shaped():
    memory = collectors.system_memory_success(
        **BASE, observed_at=160, cadence_seconds=60, source_id="host",
        used_bytes=6, capacity_bytes=7,
    )

    assert dict(memory.observations[0].payload) == {"used_bytes": 6, "capacity_bytes": 7}


def test_cpu_carries_a_bounded_normalized_binary_map():
    cpu = collectors.cpu_success(
        **BASE, observed_at=101, cadence_seconds=1, source_id="web",
        process_percent=5, system_percent=20,
        process_cpu_percent={"python": 4, "node": 3},
    )

    assert dict(cpu.observations[0].payload["process_cpu_percent"]) == {"python": 4, "node": 3}
    with pytest.raises(ValueError, match="at most 4"):
        collectors.cpu_success(
            **BASE, observed_at=101, cadence_seconds=1, source_id="web",
            process_percent=5, system_percent=20,
            process_cpu_percent={f"process-{index}": index for index in range(5)},
        )


def test_system_memory_carries_a_bounded_binary_memory_map():
    memory = collectors.system_memory_success(
        **BASE, observed_at=160, cadence_seconds=60, source_id="host",
        used_bytes=6, capacity_bytes=7,
        process_memory_bytes={"python": 4, "node": 3},
    )

    assert dict(memory.observations[0].payload["process_memory_bytes"]) == {"python": 4, "node": 3}
    # Eight-entry rows were persisted before the display was narrowed to top five. They remain
    # readable, while nine is still rejected as outside the historical wire contract.
    with pytest.raises(ValueError, match="at most 8"):
        collectors.system_memory_success(
            **BASE, observed_at=160, cadence_seconds=60, source_id="host",
            used_bytes=6, capacity_bytes=7,
            process_memory_bytes={f"process-{index}": index + 1 for index in range(9)},
        )


@pytest.mark.parametrize(
    "processes",
    [
        {"/usr/bin/python": 1},
        {"Python3": 1},
        {"python": True},
        {"python": -1},
        {"python": float("inf")},
    ],
)
def test_system_memory_rejects_unbounded_or_invalid_binary_memory_rows(processes):
    with pytest.raises(ValueError):
        collectors.system_memory_success(
            **BASE, observed_at=160, cadence_seconds=60, source_id="host",
            used_bytes=6, capacity_bytes=7, process_memory_bytes=processes,
        )


@pytest.mark.parametrize(
    ("builder", "cadence"),
    [
        ("cpu", 10),
        ("agent_status", 1),
        ("gpu", 1),
        ("service_load", 10),
        ("system_memory", 10),
    ],
)
def test_fixed_family_collectors_reject_a_cadence_not_owned_by_the_manifest(builder, cadence):
    builders = {
        "cpu": lambda: collectors.cpu_success(
            **BASE, observed_at=101, cadence_seconds=cadence, source_id="web",
            process_percent=1, system_percent=2,
        ),
        "agent_status": lambda: collectors.agent_status_success(
            **BASE, observed_at=110, cadence_seconds=cadence, source_id="scan", states={},
        ),
        "gpu": lambda: collectors.gpu_devices_success(
            (collectors.GpuDeviceSample("gpu:0", 1, 2, 3, "GPU 0"),),
            **BASE, observed_at=110, cadence_seconds=cadence,
        ),
        "service_load": lambda: collectors.service_load_success(
            (collectors.ServiceLoadSample("statsd", True, 1, 2),),
            **BASE, observed_at=110, cadence_seconds=cadence,
        ),
        "system_memory": lambda: collectors.system_memory_success(
            **BASE, observed_at=160, cadence_seconds=cadence, source_id="host",
            used_bytes=1, capacity_bytes=2,
        ),
    }
    with pytest.raises(ValueError, match="cadence_seconds must be one of"):
        builders[builder]()

    if builder == "gpu":
        with pytest.raises(ValueError, match="cadence_seconds must be one of"):
            collectors.gpu_devices_success(
                (), **BASE, observed_at=110, cadence_seconds=cadence,
            )


def test_usage_accepts_only_its_watched_or_idle_cadence_while_browser_is_event_driven():
    for cadence in (10, 60):
        facts = collectors.usage_scan_success(
            (), **BASE, observed_at=110, cadence_seconds=cadence, source_id="usage-scan",
        )
        assert facts.coverage_epochs[0].native_cadence_seconds == cadence
    with pytest.raises(ValueError, match="10, 60"):
        collectors.usage_scan_success(
            (), **BASE, observed_at=110, cadence_seconds=1, source_id="usage-scan",
        )
    event = collectors.browser_event_success(
        **BASE, observed_at=105, cadence_seconds=2.5, source_id="client:hashed",
        event_key="request", kind="api",
    )
    assert event.coverage_epochs[0].native_cadence_seconds == 2.5


def test_retry_uses_one_stable_identity_and_storage_deduplicates_it(tmp_path):
    first = collectors.cpu_success(
        **BASE, observed_at=101, cadence_seconds=1, source_id="private-stable-source",
        process_percent=1, system_percent=2,
    )
    retry = collectors.cpu_success(
        **BASE, observed_at=101, cadence_seconds=1, source_id="private-stable-source",
        process_percent=1, system_percent=2,
    )
    assert first == retry
    assert "private-stable-source" not in first.observations[0].event_id
    with Store.open(tmp_path / DATABASE_FILENAME) as store:
        result = store.append_batch(
            observations=(first.observations[0], retry.observations[0]),
            coverage_epochs=first.coverage_epochs,
        )
    assert result.observations_accepted == result.observations_duplicate == 1


def test_absent_devices_services_and_agents_never_gain_placeholder_values():
    gpu = collectors.gpu_devices_success(
        (), **BASE, observed_at=110, cadence_seconds=10,
    )
    services = collectors.service_load_success(
        (), **BASE, observed_at=110, cadence_seconds=1,
    )
    agents = collectors.agent_status_success(
        **BASE, observed_at=110, cadence_seconds=10, source_id="scan", states={},
    )
    assert gpu == collectors.CollectorFacts()
    assert services == collectors.CollectorFacts()
    assert dict(agents.observations[0].payload) == {"states": {}, "session_states": {}, "snapshot_revision": 0}


def test_successful_empty_usage_scan_is_coverage_not_fabricated_usage():
    empty = collectors.usage_scan_success(
        (), **BASE, observed_at=110, cadence_seconds=10, source_id="usage-scan",
    )
    assert empty.observations == empty.usage_atoms == ()
    assert empty.coverage_epochs[0].family == "agent_tokens"
    assert empty.coverage_epochs[0].ended_at == 120
    populated = collectors.usage_scan_success(
        (_usage(),), **BASE, observed_at=110, cadence_seconds=10, source_id="usage-scan",
    )
    assert populated.usage_atoms[0].cache_role == "read"
    assert isinstance(populated.usage_atoms[0].payload, MappingProxyType)


def test_usage_budget_exhaustion_follow_up_is_receipt_bound_and_opt_in():
    receipt = collectors.CollectorReceipt(lambda: None, lambda: None)
    normal = collectors.usage_scan_success(
        (), receipt=receipt, **BASE, observed_at=110, cadence_seconds=10,
        source_id="usage-scan",
    )
    exhausted = collectors.usage_scan_success(
        (), receipt=receipt, **BASE, observed_at=110, cadence_seconds=10,
        source_id="usage-scan", budget_exhausted_follow_up=True,
    )

    assert normal.budget_exhausted_follow_up is False
    assert exhausted.budget_exhausted_follow_up is True
    with pytest.raises(ValueError, match="requires a receipt"):
        collectors.usage_scan_success(
            (), **BASE, observed_at=110, cadence_seconds=10,
            source_id="usage-scan", budget_exhausted_follow_up=True,
        )


def test_browser_event_keys_distinguish_same_timestamp_without_payload_identity():
    common = {
        **BASE,
        "observed_at": 105,
        "cadence_seconds": 5,
        "source_id": "client:hashed",
        "kind": "api",
    }
    first = collectors.browser_event_success(**common, event_key="request-a", latency_ms=1)
    second = collectors.browser_event_success(**common, event_key="request-b", latency_ms=1)
    assert first.observations[0].event_id != second.observations[0].event_id


def test_invalid_family_and_usage_inputs_fail_at_the_current_owners():
    with pytest.raises(ValueError, match="non-negative"):
        collectors.cpu_success(
            **BASE, observed_at=101, cadence_seconds=1, source_id="web",
            process_percent=-1, system_percent=2,
        )
    invalid = _usage("bad-cache")
    invalid = UsageAtom(
        invalid.event_id, invalid.direction, invalid.modality, "cached",
        invalid.unit, invalid.observed_at, invalid.payload,
    )
    with pytest.raises(ValueError, match="cache_role"):
        collectors.usage_scan_success(
            (invalid,), **BASE, observed_at=110, cadence_seconds=10, source_id="scan",
        )


def test_epoch_close_uses_the_declared_coverage_owner():
    closed = collectors.close_epoch(
        family="cost", source_id="usage-scan", epoch_id="scan-1",
        epoch_started_at=100, ended_at=120, cadence_seconds=10, owner_generation=8,
    )
    coverage = closed.coverage_epochs[0]
    assert coverage.family == "agent_tokens"
    assert coverage.ended_at == 120
