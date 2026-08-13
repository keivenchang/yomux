"""Darwin native-memory behavioral contracts retained under tests.test_app node IDs."""

from types import SimpleNamespace

import pytest

from yolomux_lib import app as app_module


def assert_darwin_memory_details_match_one_native_vm_snapshot(monkeypatch):
    counters = app_module.DarwinVmStatistics64()
    counters.internal_page_count = 30
    counters.wire_count = 10
    counters.compressor_page_count = 20
    counters.external_page_count = 40
    monkeypatch.setattr(app_module.sys, "platform", "darwin")
    monkeypatch.setattr(app_module, "current_darwin_vm_statistics", lambda: (1000, 10, counters))
    monkeypatch.setattr(app_module, "darwin_sysctl_structure", lambda name, value_type: SimpleNamespace(used_bytes=25))
    native_values = {
        "kern.memorystatus_level": 80,
        "kern.memorystatus_vm_pressure_level": 2,
    }
    monkeypatch.setattr(app_module, "darwin_sysctl_value", lambda name, value_type: native_values.get(name))

    assert app_module.current_darwin_system_memory_details() == app_module.DarwinSystemMemoryDetails(
        physical_memory_bytes=1000,
        memory_used_bytes=600,
        cached_files_bytes=400,
        app_memory_bytes=300,
        wired_memory_bytes=100,
        compressed_memory_bytes=200,
        swap_used_bytes=25,
        pressure_percent=20.0,
        pressure_level=2,
    )


def assert_darwin_memory_details_leave_unavailable_swap_and_pressure_empty(monkeypatch):
    counters = app_module.DarwinVmStatistics64()
    monkeypatch.setattr(app_module.sys, "platform", "darwin")
    monkeypatch.setattr(app_module, "current_darwin_vm_statistics", lambda: (1000, 10, counters))
    monkeypatch.setattr(app_module, "darwin_sysctl_structure", lambda name, value_type: None)
    monkeypatch.setattr(app_module, "darwin_sysctl_value", lambda name, value_type: None)

    details = app_module.current_darwin_system_memory_details()

    assert details is not None
    assert details.swap_used_bytes is None
    assert details.pressure_percent is None
    assert details.pressure_level is None


def assert_darwin_memory_details_accept_only_native_pressure_states(monkeypatch, native_level, expected):
    counters = app_module.DarwinVmStatistics64()
    monkeypatch.setattr(app_module.sys, "platform", "darwin")
    monkeypatch.setattr(app_module, "current_darwin_vm_statistics", lambda: (1000, 10, counters))
    monkeypatch.setattr(app_module, "darwin_sysctl_structure", lambda name, value_type: None)
    monkeypatch.setattr(
        app_module,
        "darwin_sysctl_value",
        lambda name, value_type: native_level if name == "kern.memorystatus_vm_pressure_level" else 80,
    )

    details = app_module.current_darwin_system_memory_details()

    assert details is not None
    assert details.pressure_percent == 20.0
    assert details.pressure_level == expected
