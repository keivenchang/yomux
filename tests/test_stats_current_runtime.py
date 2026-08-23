# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Current stats owner lifecycle and append contracts."""

import threading
import time

import pytest

from yolomux_lib.local_services.rpc import LOCAL_RPC_MAX_METADATA_BYTES
from yolomux_lib.stats_current import client as client_module
from yolomux_lib.stats_current import collectors, runtime, scheduler, storage


class FakeClient:
    def __init__(self):
        self.leases = 0
        self.releases = []
        self.appends = []

    def acquire_lease(self):
        self.leases += 1
        return {"ok": True, "lease_id": f"lease-{self.leases}"}

    def renew_lease(self, lease_id):
        return {"ok": True, "lease_id": lease_id}

    def release_lease(self, lease_id):
        self.releases.append(lease_id)
        return {"ok": True}

    def register_collector_context(self, *, pid, port, owner_generation, control_socket):
        self.registered_control_socket = control_socket
        return {"ok": True, "pid": pid, "port": port, "owner_generation": owner_generation}

    def append(self, **groups):
        self.appends.append(groups)
        return {"ok": True}

    def status(self):
        return {"ok": True, "source_generation": len(self.appends)}


def complete_collectors(callback):
    return {family: callback for family in runtime.WEB_COLLECTED_FAMILIES}


def wait_until(predicate, timeout=1.0):
    deadline = time.monotonic() + timeout
    waiter = threading.Event()
    while time.monotonic() < deadline:
        if predicate():
            return True
        waiter.wait(0.005)
    return bool(predicate())


def test_runtime_requires_every_web_owned_collector_exactly_once():
    with pytest.raises(runtime.CurrentRuntimeError, match="collector set mismatch"):
        runtime.StatsCurrentRuntime(
            FakeClient(),
            {"agent_status": lambda _attempt: collectors.CollectorFacts()},
            owner_generation=lambda: 1,
            token_cadence_seconds=lambda: 10,
        )


def test_runtime_uses_one_demand_cadence_parent_for_every_web_scheduled_family():
    current = runtime.StatsCurrentRuntime(
        FakeClient(),
        complete_collectors(lambda _attempt: collectors.CollectorFacts()),
        owner_generation=lambda: 1,
        token_cadence_seconds=lambda: 10,
        family_cadence_seconds=lambda family: 60,
    )

    assert "cpu" not in current.scheduler._workers
    assert "gpu" not in current.scheduler._workers
    assert current.scheduler._cadence(current.scheduler._workers["agent_tokens"].job) == 60


def test_runtime_persists_scheduler_deadline_misses_for_known_sources():
    client = FakeClient()
    current = runtime.StatsCurrentRuntime(
        client,
        complete_collectors(lambda _attempt: collectors.CollectorFacts()),
        owner_generation=lambda: 7,
        token_cadence_seconds=lambda: 10,
    )
    current._append_facts("agent_status", collectors.CollectorFacts(
        coverage_epochs=(storage.CoverageEpoch("agent_status", "port:7443", "7:agent_status:1", 100, 110, 10, 7),),
    ))

    current._missed_deadline_callback("agent_status")(scheduler.CollectorMiss(
        "agent_status", "7:agent_status:1", 110, 160, 10, 7,
    ))

    assert client.appends[-1]["unavailable_spans"] == (
        storage.UnavailableSpan(
            "agent_status", "port:7443", "7:agent_status:1", 110, 160, 10,
            "scheduler_deadline_missed", 7,
        ),
    )


def test_runtime_registers_owner_identity_before_starting_collectors():
    events = []

    class ContextClient(FakeClient):
        def register_collector_context(self, *, pid, port, owner_generation, control_socket):
            events.append(("context", pid, port, owner_generation, control_socket))
            return super().register_collector_context(
                pid=pid, port=port, owner_generation=owner_generation, control_socket=control_socket,
            )

    client = ContextClient()

    def collect(_attempt):
        events.append(("collect",))
        return collectors.CollectorFacts()

    current = runtime.StatsCurrentRuntime(
        client,
        complete_collectors(collect),
        owner_generation=lambda: 7,
        token_cadence_seconds=lambda: 60,
        collector_context=lambda: {"pid": 1234, "port": 7443, "owner_generation": 7, "control_socket": "/tmp/web.sock"},
    )

    assert current.start() is True
    assert wait_until(lambda: any(event[0] == "collect" for event in events))
    current.stop()

    # The ordering contract is unchanged: identity is registered before any collector runs. The
    # registration now also carries WHERE to reach this web process, because statsd cannot push
    # the process's own CPU sample without it -- so the address belongs to the same handshake
    # this test has always guarded, not to a later one.
    assert events[0] == ("context", 1234, 7443, 7, "/tmp/web.sock")


def test_runtime_leases_before_append_and_releases_after_workers_stop():
    client = FakeClient()
    collected = threading.Event()

    def collect(attempt):
        collected.set()
        return collectors.agent_status_success(
            epoch_id=attempt.epoch_id,
            epoch_started_at=attempt.epoch_started_at,
            observed_at=attempt.scheduled_at,
                cadence_seconds=10,
            owner_generation=attempt.owner_generation,
            source_id="web",
            states={"web": "run"},
        ) if attempt.family == "agent_status" else collectors.CollectorFacts(
            coverage_epochs=(storage.CoverageEpoch(
                attempt.family,
                "web",
                attempt.epoch_id,
                attempt.epoch_started_at,
                attempt.scheduled_at + attempt.cadence_seconds,
                attempt.cadence_seconds,
                attempt.owner_generation,
            ),),
        )

    owner = 8
    current = runtime.StatsCurrentRuntime(
        client,
        complete_collectors(collect),
        owner_generation=lambda: owner,
        token_cadence_seconds=lambda: 10,
    )
    try:
        assert current.start() is True
        assert collected.wait(1)
        assert client.leases == 1
        assert current.start() is False
    finally:
        current.stop()

    assert client.releases == ["lease-1"]
    assert any(group["observations"] for group in client.appends)
    assert current.status()["leased"] is False


def test_runtime_reacquires_lease_after_daemon_replacement_without_restarting_workers():
    renewed = threading.Event()

    class ReplacedDaemonClient(FakeClient):
        def __init__(self):
            super().__init__()
            self.renewals = 0

        def renew_lease(self, lease_id):
            self.renewals += 1
            if self.renewals == 1:
                renewed.set()
                return {"ok": True, "lease_id": "replacement-lease"}
            return {"ok": True, "lease_id": lease_id}

    client = ReplacedDaemonClient()
    current = runtime.StatsCurrentRuntime(
        client,
        complete_collectors(lambda _attempt: collectors.CollectorFacts()),
        owner_generation=lambda: 1,
        token_cadence_seconds=lambda: 10,
        owner_check_seconds=0.01,
    )
    try:
        assert current.start() is True
        assert renewed.wait(1)
        assert wait_until(lambda: current._lease_id == "replacement-lease")
        assert current.status()["supervisor"]["phase"] == "running"
        assert client.leases == 1
    finally:
        current.stop()

    assert client.releases == ["replacement-lease"]


def test_retired_owner_result_is_not_appended():
    client = FakeClient()
    entered = threading.Event()
    release = threading.Event()
    owner = 1

    def collect(_attempt):
        entered.set()
        assert release.wait(1)
        return collectors.CollectorFacts(
            coverage_epochs=(storage.CoverageEpoch("cpu", "web", "epoch", 1, 2, 1, 1),),
        )

    current = runtime.StatsCurrentRuntime(
        client,
        complete_collectors(collect),
        owner_generation=lambda: owner,
        token_cadence_seconds=lambda: 10,
    )
    try:
        assert current.start() is True
        assert entered.wait(1)
        owner = 2
        release.set()
    finally:
        release.set()
        current.stop()

    assert client.appends == []


def test_usage_scan_is_batched_without_repeating_coverage():
    client = FakeClient()
    current = runtime.StatsCurrentRuntime(
        client,
        complete_collectors(lambda _attempt: collectors.CollectorFacts()),
        owner_generation=lambda: 1,
        token_cadence_seconds=lambda: 10,
    )
    atoms = tuple(
        storage.UsageAtom(
            f"event-{index}",
            "output",
            "text",
            "none",
            "tokens",
            1,
            {
                "quantity": 1,
                "provider": "openai",
                "model": "gpt",
                "agent_id": "agent",
                "telemetry_complete": True,
            },
        )
        for index in range(2_005)
    )
    coverage = storage.CoverageEpoch("agent_tokens", "web", "epoch", 1, 2, 10, 1)

    current._append_facts(
        "agent_tokens",
        collectors.CollectorFacts(coverage_epochs=(coverage,), usage_atoms=atoms),
    )

    assert sum(len(group["usage_atoms"]) for group in client.appends) == len(atoms)
    assert [len(group["coverage_epochs"]) for group in client.appends] == [1, 0, 0]
    assert all(
        client_module.append_metadata_size(
            observations=group["observations"],
            usage_atoms=group["usage_atoms"],
            coverage_epochs=group["coverage_epochs"],
        ) <= LOCAL_RPC_MAX_METADATA_BYTES
        for group in client.appends
    )


def test_append_failure_preserves_the_service_reason_for_diagnostics():
    client = FakeClient()
    client.append = lambda **_groups: {
        "status": "unsupported",
        "reason": "coverage owner_generation cannot move backward",
    }
    current = runtime.StatsCurrentRuntime(
        client,
        complete_collectors(lambda _attempt: collectors.CollectorFacts()),
        owner_generation=lambda: 1,
        token_cadence_seconds=lambda: 10,
    )

    with pytest.raises(
        runtime.CurrentRuntimeError,
        match="coverage owner_generation cannot move backward",
    ):
        current._append_facts(
            "cpu",
            collectors.CollectorFacts(
                coverage_epochs=(
                    storage.CoverageEpoch("cpu", "web", "epoch", 1, 2, 1, 1),
                ),
            ),
        )


def test_usage_conflict_isolates_poison_atoms_and_commits_clean_receipt():
    events = []

    class ConflictClient(FakeClient):
        def __init__(self):
            super().__init__()
            self.stored = []

        def append(self, **groups):
            self.appends.append(groups)
            if any(
                atom.event_id.startswith("poison-")
                for atom in groups["usage_atoms"]
            ):
                return {
                    "ok": False,
                    "status": storage.USAGE_IDENTITY_CONFLICT_STATUS,
                }
            self.stored.append(groups)
            return {"ok": True}

    client = ConflictClient()
    current = runtime.StatsCurrentRuntime(
        client,
        complete_collectors(lambda _attempt: collectors.CollectorFacts()),
        owner_generation=lambda: 1,
        token_cadence_seconds=lambda: 10,
    )
    atoms = tuple(
        storage.UsageAtom(
            event_id, "output", "text", "none", "tokens", 10.0,
            {
                "quantity": 1,
                "provider": "openai",
                "model": "gpt",
                "agent_id": "agent",
                "telemetry_complete": True,
            },
        )
        for event_id in (
            "clean-1", "poison-1", "clean-2", "clean-3", "poison-2", "clean-4",
        )
    )
    coverage = storage.CoverageEpoch(
        "agent_tokens", "web", "epoch", 1, 2, 10, 1,
    )
    tombstone = storage.UsageAtomTombstone(
        "codex:thread:1", "input", "text", "none", "tokens", 9.0, 1.0,
        "openai", "gpt", "thread",
    )
    receipt = collectors.CollectorReceipt(
        lambda: events.append("commit"),
        lambda: events.append("rollback"),
    )

    current._append_facts(
        "agent_tokens",
        collectors.CollectorFacts(
            usage_atoms=atoms,
            usage_tombstones=(tombstone,),
            coverage_epochs=(coverage,),
            receipt=receipt,
        ),
    )

    stored_atoms = [
        atom.event_id
        for group in client.stored
        for atom in group["usage_atoms"]
    ]
    assert stored_atoms == ["clean-1", "clean-2", "clean-3", "clean-4"]
    assert sum(len(group["coverage_epochs"]) for group in client.stored) == 1
    assert client.stored[-1]["usage_tombstones"] == (tombstone,)
    assert events == ["commit"]
    assert any(len(group["usage_atoms"]) > 1 for group in client.appends)
    assert sum(
        len(group["usage_atoms"]) == 1
        and group["usage_atoms"][0].event_id.startswith("poison-")
        for group in client.appends
    ) == 2

    current._append_facts(
        "agent_tokens",
        collectors.CollectorFacts(usage_atoms=(atoms[1],), receipt=receipt),
    )
    assert events == ["commit", "commit"]


def test_usage_conflict_without_an_atom_remains_a_hard_failure():
    class InvalidConflictClient(FakeClient):
        def append(self, **groups):
            self.appends.append(groups)
            return {
                "ok": False,
                "status": storage.USAGE_IDENTITY_CONFLICT_STATUS,
            }

    current = runtime.StatsCurrentRuntime(
        InvalidConflictClient(),
        complete_collectors(lambda _attempt: collectors.CollectorFacts()),
        owner_generation=lambda: 1,
        token_cadence_seconds=lambda: 10,
    )

    with pytest.raises(
        runtime.CurrentRuntimeError,
        match="without a usage atom",
    ):
        current._append_facts(
            "agent_tokens",
            collectors.CollectorFacts(
                coverage_epochs=(storage.CoverageEpoch(
                    "agent_tokens", "web", "epoch", 1, 2, 10, 1,
                ),),
            ),
        )


def test_budget_exhaustion_follow_up_wakes_same_family_after_append_and_commit(monkeypatch):
    events = []

    class OrderedClient(FakeClient):
        def append(self, **groups):
            events.append("append")
            return super().append(**groups)

    current = runtime.StatsCurrentRuntime(
        OrderedClient(),
        complete_collectors(lambda _attempt: collectors.CollectorFacts()),
        owner_generation=lambda: 1,
        token_cadence_seconds=lambda: 10,
    )
    def record_wake(family, *, min_interval_seconds=0.0):
        events.append(f"wake:{family}:{min_interval_seconds}")
        return True

    monkeypatch.setattr(current.scheduler, "wake", record_wake)
    coverage = storage.CoverageEpoch(
        "agent_tokens", "web", "epoch", 1, 2, 10, 1,
    )
    exhausted = collectors.CollectorFacts(
        coverage_epochs=(coverage,),
        receipt=collectors.CollectorReceipt(
            lambda: events.append("commit"),
            lambda: events.append("rollback"),
        ),
        budget_exhausted_follow_up=True,
    )

    current._append_facts("agent_tokens", exhausted)

    # The follow-up must carry its rate floor: an unfloored self-wake let the
    # live agent_tokens worker free-run at 41 cycles in 30s against a 10s cadence.
    assert events == [
        "append",
        "commit",
        f"wake:agent_tokens:{runtime.BUDGET_FOLLOW_UP_MIN_INTERVAL_SECONDS}",
    ]
    assert runtime.BUDGET_FOLLOW_UP_MIN_INTERVAL_SECONDS > 0

    events.clear()
    normal = collectors.CollectorFacts(
        coverage_epochs=(coverage,),
        receipt=collectors.CollectorReceipt(
            lambda: events.append("commit"),
            lambda: events.append("rollback"),
        ),
    )
    current._append_facts("agent_tokens", normal)
    assert events == ["append", "commit"]


def test_budget_exhaustion_follow_up_does_not_wake_after_append_failure(monkeypatch):
    events = []

    class FailingClient(FakeClient):
        def append(self, **groups):
            self.appends.append(groups)
            events.append("append")
            return {"ok": False, "reason": "append rejected"}

    current = runtime.StatsCurrentRuntime(
        FailingClient(),
        complete_collectors(lambda _attempt: collectors.CollectorFacts()),
        owner_generation=lambda: 1,
        token_cadence_seconds=lambda: 10,
    )
    monkeypatch.setattr(
        current.scheduler,
        "wake",
        lambda family: events.append(f"wake:{family}") or True,
    )
    facts = collectors.CollectorFacts(
        coverage_epochs=(storage.CoverageEpoch(
            "agent_tokens", "web", "epoch", 1, 2, 10, 1,
        ),),
        receipt=collectors.CollectorReceipt(
            lambda: events.append("commit"),
            lambda: events.append("rollback"),
        ),
        budget_exhausted_follow_up=True,
    )

    with pytest.raises(runtime.CurrentRuntimeError, match="append rejected"):
        current._append_facts("agent_tokens", facts)

    assert events == ["append", "rollback"]


def test_usage_scan_batches_by_encoded_wire_size_before_record_count():
    client = FakeClient()
    current = runtime.StatsCurrentRuntime(
        client,
        complete_collectors(lambda _attempt: collectors.CollectorFacts()),
        owner_generation=lambda: 1,
        token_cadence_seconds=lambda: 10,
    )
    long_identity = "x" * 256
    atoms = tuple(
        storage.UsageAtom(
            f"event-{index}", "output", "text", "none", "tokens", 1,
            {
                "quantity": 1,
                "provider": long_identity,
                "model": long_identity,
                "agent_id": long_identity,
                "source_file": long_identity,
                "source_type": long_identity,
                "session_id": long_identity,
                "telemetry_complete": True,
            },
        )
        for index in range(1_000)
    )

    current._append_facts(
        "agent_tokens",
        collectors.CollectorFacts(usage_atoms=atoms),
    )

    assert len(client.appends) > 1
    assert sum(len(group["usage_atoms"]) for group in client.appends) == len(atoms)
    assert all(
        client_module.append_metadata_size(
            observations=group["observations"],
            usage_atoms=group["usage_atoms"],
            coverage_epochs=group["coverage_epochs"],
        ) <= LOCAL_RPC_MAX_METADATA_BYTES
        for group in client.appends
    )


def test_supervisor_returns_promptly_retries_with_bounded_backoff_and_starts_once():
    private_failure = "socket /private/path contains private-client-id"
    collected = threading.Event()

    class RecoveringClient(FakeClient):
        def __init__(self):
            super().__init__()
            self.attempted_at = []

        def acquire_lease(self):
            self.leases += 1
            self.attempted_at.append(time.monotonic())
            if self.leases < 3:
                return {"ok": False, "error": private_failure}
            return {"ok": True, "lease_id": f"lease-{self.leases}"}

        def status(self):
            return {"ok": False, "status": "unavailable", "error": private_failure}

    client = RecoveringClient()
    current = runtime.StatsCurrentRuntime(
        client,
        complete_collectors(lambda _attempt: (collected.set(), collectors.CollectorFacts())[1]),
        owner_generation=lambda: 1,
        token_cadence_seconds=lambda: 10,
        retry_initial_seconds=0.02,
        retry_max_seconds=0.04,
        owner_check_seconds=0.01,
    )
    started = time.monotonic()
    try:
        assert current.start() is True
        assert time.monotonic() - started < 0.1
        assert current.start() is False
        assert collected.wait(1)
        assert wait_until(lambda: current.status()["supervisor"]["phase"] == "running")
        status = current.status()
        assert status["leased"] is True
        assert status["supervisor"] == {
            "phase": "running",
            "alive": True,
            "failure_count": 2,
            "last_failure": "",
            "retry_delay_seconds": 0.0,
            "retry_in_seconds": 0.0,
        }
        assert status["service"] == {"ok": False, "status": "unavailable"}
        assert private_failure not in str(status)
        assert len(client.attempted_at) == 3
        assert client.attempted_at[1] - client.attempted_at[0] >= 0.015
        assert client.attempted_at[2] - client.attempted_at[1] >= 0.035
    finally:
        current.stop()

    assert client.releases == ["lease-3"]


def test_start_does_not_wait_for_a_blocked_daemon_attempt():
    entered = threading.Event()
    release = threading.Event()

    class BlockingClient(FakeClient):
        def acquire_lease(self):
            self.leases += 1
            entered.set()
            assert release.wait(1)
            return {"ok": False, "error": "temporary"}

    client = BlockingClient()
    current = runtime.StatsCurrentRuntime(
        client,
        complete_collectors(lambda _attempt: collectors.CollectorFacts()),
        owner_generation=lambda: 1,
        token_cadence_seconds=lambda: 10,
        retry_initial_seconds=0.2,
        retry_max_seconds=0.4,
        owner_check_seconds=0.01,
    )
    started = time.monotonic()
    assert current.start() is True
    assert time.monotonic() - started < 0.1
    assert entered.wait(1)
    assert current.status()["supervisor"]["phase"] == "acquiring_lease"
    release.set()
    current.stop()

    assert client.leases == 1
    assert client.releases == []


def test_stop_cancels_backoff_without_another_launch_attempt():
    failed = threading.Event()

    class UnavailableClient(FakeClient):
        def acquire_lease(self):
            self.leases += 1
            failed.set()
            return {"ok": False, "error": "temporary"}

    client = UnavailableClient()
    current = runtime.StatsCurrentRuntime(
        client,
        complete_collectors(lambda _attempt: collectors.CollectorFacts()),
        owner_generation=lambda: 1,
        token_cadence_seconds=lambda: 10,
        retry_initial_seconds=0.2,
        retry_max_seconds=0.4,
        owner_check_seconds=0.01,
    )
    assert current.start() is True
    assert failed.wait(1)
    assert wait_until(lambda: current.status()["supervisor"]["phase"] == "backoff")
    current.stop()
    attempts_after_stop = client.leases
    threading.Event().wait(0.25)

    assert client.leases == attempts_after_stop == 1
    assert client.releases == []
    assert current.status()["supervisor"]["phase"] == "stopped"
    assert current.status()["supervisor"]["alive"] is False


def test_upgrade_fence_is_terminal_instead_of_a_runaway_retry_loop():
    class FutureServiceClient(FakeClient):
        def acquire_lease(self):
            self.leases += 1
            return {
                "ok": False,
                "status": "upgrade_required",
                "required_protocol_version": 24,
            }

    client = FutureServiceClient()
    current = runtime.StatsCurrentRuntime(
        client,
        complete_collectors(lambda _attempt: collectors.CollectorFacts()),
        owner_generation=lambda: 1,
        token_cadence_seconds=lambda: 10,
        retry_initial_seconds=0.01,
        retry_max_seconds=0.02,
        owner_check_seconds=0.01,
    )
    try:
        assert current.start() is True
        assert wait_until(lambda: current.status()["supervisor"]["phase"] == "blocked")
        threading.Event().wait(0.05)
        assert client.leases == 1
        assert current.start() is False
        assert current.status()["supervisor"]["last_failure"] == "UpgradeRequired"
    finally:
        current.stop()


def test_demotion_stops_workers_before_release_and_reacquires_without_duplicates():
    owner = [1]
    order = []

    class OrderedClient(FakeClient):
        def acquire_lease(self):
            order.append("lease")
            return super().acquire_lease()

        def release_lease(self, lease_id):
            order.append("release")
            return super().release_lease(lease_id)

    class OrderedScheduler:
        def __init__(self):
            self.starts = 0
            self.stops = 0
            self.running = False

        def start(self):
            assert self.running is False
            order.append("scheduler")
            self.starts += 1
            self.running = True
            return True

        def stop(self):
            if self.running:
                order.append("workers-stopped")
                self.stops += 1
                self.running = False

        def wake(self, _family):
            return False

        def status(self):
            return {}

    client = OrderedClient()
    current = runtime.StatsCurrentRuntime(
        client,
        complete_collectors(lambda _attempt: collectors.CollectorFacts()),
        owner_generation=lambda: owner[0],
        token_cadence_seconds=lambda: 10,
        retry_initial_seconds=0.02,
        retry_max_seconds=0.04,
        owner_check_seconds=0.01,
    )
    owned_scheduler = OrderedScheduler()
    current.scheduler = owned_scheduler
    try:
        assert current.start() is True
        assert wait_until(lambda: current.status()["supervisor"]["phase"] == "running")
        assert order[:2] == ["lease", "scheduler"]
        assert owned_scheduler.starts == 1

        owner[0] = None
        assert wait_until(lambda: current.status()["supervisor"]["phase"] == "waiting_owner")
        assert order[2:4] == ["workers-stopped", "release"]
        assert client.leases == owned_scheduler.starts == 1

        owner[0] = 2
        assert wait_until(lambda: owned_scheduler.starts == 2)
        assert current.status()["supervisor"]["phase"] == "running"
        assert client.leases == owned_scheduler.starts == 2
    finally:
        current.stop()

    assert order[-2:] == ["workers-stopped", "release"]
    assert client.releases == ["lease-1", "lease-2"]
