# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Host CPU sampling backs off only while nobody is watching stats.

The saving is real but it is bounded by one hard requirement: one-second in-memory UI freshness
is not negotiable. So the contract is not "sample less" -- it is "sample less ONLY while
unwatched, and resume immediately the instant a watcher returns", which is a STATE TRANSITION and
cannot be established by asserting a steady state in each mode separately.
"""

from __future__ import annotations

from yomux_lib.stats_current import host_collectors
from yomux_lib.stats_current import service as service_module


class _Store:
    """Minimal publisher: the sampler only appends, and these tests read the deadline."""

    def __init__(self) -> None:
        self.appends = 0

    def append_batch(self, **values):
        self.appends += 1
        return service_module.storage.AppendResult(1, 1, 0, 0, 0, 0, 0)

    def latest_coverage_epoch(self, family, source_id, owner_generation, cadence):
        """Declared, not absent: the sampler reuses a retained epoch rather than probing for one."""
        return None


class _Sampler:
    def __init__(self) -> None:
        self.calls = 0

    def sample(self, pid):
        self.calls += 1
        return {
            "time": 100.0, "pid": pid, "cpu_percent": 12.0,
            "system_cpu_percent": 20.0, "rss_bytes": 99,
        }


def _service(tmp_path, monkeypatch, clock):
    service = service_module.StatsCurrentService(
        tmp_path / "stats.sock", tmp_path / "stats.sqlite3",
        monotonic=lambda: clock[0],
    )
    service.collector_context = {"pid": 1234, "port": 7443, "owner_generation": 42}
    service._host_cpu_sampler = _Sampler()
    service._next_host_gpu_at = float("inf")
    monkeypatch.setattr(service, "_web_push_target", lambda: ({"control_socket": "s"}, ""))
    monkeypatch.setattr(service_module, "send_yomux_control_request",
                        lambda owner, request, timeout: {"ok": True})
    return service


def _watch(service, clock):
    """One RPC, which is what a live delta-stream frame does on the real path."""
    service._on_client()


def test_a_fresh_daemon_is_watched_rather_than_defaulting_to_backed_off(tmp_path, monkeypatch):
    """No silent default. An unavailable signal must not be read as 'nobody is watching'.

    `last_rpc_at` is stamped at construction, so a daemon that has served nothing yet is inside
    its window and samples at one second. Defaulting the other way would make every cold start
    publish an absent CPU sample until the first RPC arrived.
    """
    clock = [1_000.0]
    service = _service(tmp_path, monkeypatch, clock)

    assert service._stats_are_watched() is True
    assert service._host_cpu_cadence_seconds() == host_collectors.HOST_CPU_CADENCE_SECONDS


def test_the_sampler_holds_one_second_cadence_while_stats_are_watched(tmp_path, monkeypatch):
    clock = [1_000.0]
    service = _service(tmp_path, monkeypatch, clock)
    store = _Store()
    service._next_host_cpu_at = clock[0]

    _watch(service, clock)
    service._collect_host_facts_if_due(store)

    assert service._stats_are_watched() is True
    assert service._next_host_cpu_at == clock[0] + host_collectors.HOST_CPU_CADENCE_SECONDS


def test_the_sampler_backs_off_while_stats_are_unwatched(tmp_path, monkeypatch):
    """The saving. Nobody is reading, so the next sample is deliberately far away."""
    clock = [1_000.0]
    service = _service(tmp_path, monkeypatch, clock)
    store = _Store()

    _watch(service, clock)
    clock[0] += service_module.HOST_CPU_UNWATCHED_AFTER_SECONDS + 1.0
    service._next_host_cpu_at = clock[0]
    service._collect_host_facts_if_due(store)

    assert service._stats_are_watched() is False
    assert service._next_host_cpu_at == clock[0] + service_module.HOST_CPU_UNWATCHED_CADENCE_SECONDS


def test_watched_then_unwatched_then_watched_resumes_without_waiting_out_the_stretched_deadline(
    tmp_path, monkeypatch,
):
    """THE transition, and the whole point of the item.

    A steady-state assertion in each mode would pass even if a returning watcher had to wait out
    the backed-off deadline -- which is the failure that breaks one-second freshness, because
    nothing else moves `_next_host_cpu_at` back.
    """
    clock = [1_000.0]
    service = _service(tmp_path, monkeypatch, clock)
    store = _Store()

    # watched
    _watch(service, clock)
    service._next_host_cpu_at = clock[0]
    service._collect_host_facts_if_due(store)
    assert service._next_host_cpu_at == clock[0] + host_collectors.HOST_CPU_CADENCE_SECONDS

    # -> unwatched: the deadline stretches
    clock[0] += service_module.HOST_CPU_UNWATCHED_AFTER_SECONDS + 1.0
    service._next_host_cpu_at = clock[0]
    service._collect_host_facts_if_due(store)
    stretched = service._next_host_cpu_at
    assert stretched == clock[0] + service_module.HOST_CPU_UNWATCHED_CADENCE_SECONDS

    # -> watched again, one second later. The stretched deadline must NOT stand.
    clock[0] += 1.0
    _watch(service, clock)
    service._collect_host_facts_if_due(store)

    assert service._stats_are_watched() is True
    assert service._next_host_cpu_at < stretched, (
        "a returning watcher had to wait out the backed-off deadline"
    )
    assert service._next_host_cpu_at == clock[0] + host_collectors.HOST_CPU_CADENCE_SECONDS
    assert service._host_cpu_sampler.calls == 3


def test_a_returning_watcher_wakes_the_worker_instead_of_sleeping_on_the_stretched_deadline(
    tmp_path, monkeypatch,
):
    """Pulling the deadline forward is not enough on its own.

    `_ring_wait_timeout` sleeps until `min(deadlines)`, which includes `_next_host_cpu_at`, and
    `_on_client` is the only thing that observes a watcher returning. If it does not wake the
    worker, the worker sleeps out the backed-off interval and the pulled-forward deadline is
    never read.
    """
    clock = [1_000.0]
    service = _service(tmp_path, monkeypatch, clock)

    _watch(service, clock)
    service.work_event.clear()

    # A second RPC inside the window is ordinary traffic and must not wake the worker.
    clock[0] += 1.0
    service._on_client()
    assert service.work_event.is_set() is False

    # A return after the backoff window is a transition, and must.
    clock[0] += service_module.HOST_CPU_UNWATCHED_AFTER_SECONDS + 1.0
    service._on_client()
    assert service.work_event.is_set() is True
