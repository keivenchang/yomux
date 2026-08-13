# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Characterization of split local-service registry lifecycle state."""

import threading
from types import SimpleNamespace

import pytest

from yolomux_lib.local_services import registry as registry_module
from yolomux_lib.local_services.registry import ChildOwnershipState
from yolomux_lib.local_services.registry import HealthProbeCache
from yolomux_lib.local_services.registry import LOCAL_SERVICE_HEALTH_CACHE_SECONDS
from yolomux_lib.local_services.registry import LocalServiceRegistry
from yolomux_lib.local_services.registry import LocalServiceSpec
from yolomux_lib.local_services.registry import StartupFailureState


def test_startup_failure_state_resets_one_complete_episode():
    state = StartupFailureState(
        failures=3,
        next_start_at=42.0,
        start_exit_count=2,
        last_exit_code=7,
        failure_reason="failed",
        record_refusal_reason="refused",
        terminal_failure=True,
    )

    state.reset()

    assert state == StartupFailureState()


def test_health_probe_cache_success_expiry_and_invalidation():
    cache = HealthProbeCache()

    cache.note_success(10.0)

    assert cache.healthy_until == 10.0 + LOCAL_SERVICE_HEALTH_CACHE_SECONDS
    assert cache.is_recent(cache.healthy_until - 0.001) is True
    assert cache.is_recent(cache.healthy_until) is False
    cache.invalidate()
    assert cache == HealthProbeCache()


def test_child_ownership_state_starts_without_a_child_or_reaper():
    state = ChildOwnershipState()

    assert state.process is None
    assert state.spawn_ownership is None
    assert state.adopted_reaper_pid == 0


def _fixture_registry(tmp_path):
    return LocalServiceRegistry(
        tmp_path,
        LocalServiceSpec("fixture", "fixture.module", "fixture.sock", 1),
    )


def test_child_reaper_handle_is_retained_until_settlement(tmp_path, monkeypatch):
    registry = _fixture_registry(tmp_path)
    release = threading.Event()
    monkeypatch.setattr(registry, "_reap_exited_child", lambda _process: release.wait())

    registry._start_child_reaper(SimpleNamespace())

    assert tuple(thread.name for thread in registry._child_ownership.reaper_threads) == ("fixture-reaper",)
    with pytest.raises(RuntimeError, match="fixture-reaper"):
        registry.settle_reaper_threads(timeout=0)
    release.set()
    registry.settle_reaper_threads()
    assert registry._child_ownership.reaper_threads == set()


def test_adopted_reaper_handle_is_retained_until_settlement(tmp_path, monkeypatch):
    registry = _fixture_registry(tmp_path)
    release = threading.Event()
    monkeypatch.setattr(registry, "_read_record", lambda: {"pid": 43210})
    monkeypatch.setattr(
        registry_module,
        "process_record_diagnostic",
        lambda *_args, **_kwargs: SimpleNamespace(current=True),
    )
    monkeypatch.setattr(registry, "_reap_adopted_child", lambda _pid: release.wait())

    registry._arm_adopted_reaper()

    assert tuple(thread.name for thread in registry._child_ownership.reaper_threads) == (
        "fixture-adopted-reaper",
    )
    release.set()
    registry.settle_reaper_threads()
    assert registry._child_ownership.reaper_threads == set()
