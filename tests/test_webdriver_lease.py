# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""The shared WebDriver lease: one generation proof, one bounded proof-guarded retirement.

Every enumerated W10 WebDriver case is here as a red-then-green unit against injected process
primitives, so no real Chrome is spawned: normal / hung / raising quit, reparented descendants,
stale identity and PID reuse, permission denial, and multiple-driver cleanup.
"""

import signal
import subprocess
import time

import pytest

from tests.browser_helpers import webdriver_lease
from tests.browser_helpers.webdriver_lease import DriverGeneration
from tests.browser_helpers.webdriver_lease import WebDriverLease
from tests.browser_helpers.webdriver_lease import chromedriver_pid
from tests.browser_helpers.webdriver_lease import process_start_key
from tests.browser_helpers.webdriver_lease import retire_all


class FakeProcess:
    def __init__(self, pid):
        self.pid = pid


class FakeService:
    def __init__(self, pid):
        self.process = FakeProcess(pid)


class FakeDriver:
    """A driver whose quit() can return, hang, or raise, and that reports a chromedriver PID."""

    def __init__(self, pid=4321, *, quit_behavior="return", quit_delay=0.0):
        self.service = FakeService(pid)
        self.quit_calls = 0
        self._quit_behavior = quit_behavior
        self._quit_delay = quit_delay

    def quit(self):
        self.quit_calls += 1
        if self._quit_delay:
            time.sleep(self._quit_delay)
        if self._quit_behavior == "raise":
            raise RuntimeError("synthetic quit failure")


class World:
    """An injectable process table: which PIDs are alive and with which immutable start key."""

    def __init__(self, alive):
        # alive: {pid: start_key}
        self.alive = dict(alive)
        self.signals = []

    def identity(self, pid):
        return self.alive.get(pid)

    def signal(self, pid, sig):
        self.signals.append((pid, sig))
        if pid not in self.alive:
            raise ProcessLookupError(pid)
        # A real KILL removes the process; a TERM here also clears it so escalation is bounded.
        if sig in (signal.SIGTERM, signal.SIGKILL):
            del self.alive[pid]


def make_lease(driver, world, *, pid=4321, start_key="gen-1", **kwargs):
    return WebDriverLease(
        driver,
        generation=DriverGeneration(pid=pid, start_key=start_key),
        quit_timeout=kwargs.pop("quit_timeout", 0.2),
        reap_timeout=kwargs.pop("reap_timeout", 0.5),
        poll_seconds=0.01,
        identity_fn=kwargs.pop("identity_fn", world.identity),
        signal_fn=kwargs.pop("signal_fn", world.signal),
        **kwargs,
    )


def test_chromedriver_pid_and_start_key_read_a_real_process():
    """The two primitives must work against a real OS process, not only fakes."""

    process = subprocess.Popen(["sleep", "30"])
    try:
        key = process_start_key(process.pid)
        assert key is not None and key == process_start_key(process.pid), key
    finally:
        process.kill()
        process.wait(timeout=5)
    # A PID that is not alive proves absence by returning None.
    assert process_start_key(process.pid) is None
    assert chromedriver_pid(FakeDriver(pid=99)) == 99
    assert chromedriver_pid(object()) is None


def test_start_key_reads_a_killed_but_unreaped_zombie_as_gone():
    """A killed child lingers as a zombie until its parent reaps it; retirement must read it as gone.

    On Linux /proc/<pid>/stat survives for a zombie, so a naive start-key read would keep reporting
    the leased process 'alive' and never prove retirement complete. The owner treats state Z as
    absent - it runs nothing and never will.
    """

    process = subprocess.Popen(["sleep", "60"])
    process.kill()
    # Do NOT reap yet: the process is now a zombie whose /proc entry still exists.
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and process_start_key(process.pid) is not None:
        time.sleep(0.02)
    assert process_start_key(process.pid) is None, "a zombie was read as a live owned identity"
    process.wait(timeout=5)


def test_acquire_captures_the_generation_at_spawn():
    world = World({4321: "gen-1"})
    lease = WebDriverLease.acquire(lambda: FakeDriver(pid=4321), identity_fn=world.identity, signal_fn=world.signal)
    assert lease.generation.pid == 4321 and lease.generation.start_key == "gen-1"
    assert lease.is_current_process() is True

    # from_driver captures the same generation for an already-spawned driver (the Finder-repro path).
    handed_off = WebDriverLease.from_driver(FakeDriver(pid=4321), identity_fn=world.identity, signal_fn=world.signal)
    assert handed_off.generation.pid == 4321 and handed_off.generation.start_key == "gen-1"


def test_normal_quit_retires_without_ever_signalling():
    """A clean quit that removes the process must not TERM or KILL anything."""

    world = World({4321: "gen-1"})
    driver = FakeDriver(pid=4321, quit_behavior="return")
    lease = make_lease(driver, world)
    # A returning quit clears the process, exactly as a real driver.quit() does.
    original_quit = driver.quit

    def quit_and_die():
        original_quit()
        world.alive.pop(4321, None)

    driver.quit = quit_and_die
    result = lease.retire()
    assert result.proven_gone is True, result.as_dict()
    assert result.signals_sent == [], result.as_dict()
    assert result.steps[0] == "quit:returned"
    assert result.errors == []


def test_hung_quit_escalates_to_a_bounded_kill():
    """A quit that never returns must not hang the gate; the leased PID is TERM/KILLed and proven gone."""

    world = World({4321: "gen-1"})
    driver = FakeDriver(pid=4321, quit_behavior="return", quit_delay=5.0)
    lease = make_lease(driver, world, quit_timeout=0.1)
    started = time.monotonic()
    result = lease.retire()
    assert time.monotonic() - started < 4.0, "retirement waited on the hung quit instead of bounding it"
    assert result.steps[0] == "quit:timeout"
    assert result.signals_sent and result.signals_sent[0] == "SIGTERM"
    assert result.proven_gone is True, result.as_dict()


def test_raising_quit_is_aggregated_not_thrown():
    world = World({4321: "gen-1"})
    driver = FakeDriver(pid=4321, quit_behavior="raise")
    lease = make_lease(driver, world)
    result = lease.retire()  # must not raise
    assert any("synthetic quit failure" in err for err in result.errors), result.as_dict()
    assert result.proven_gone is True, result.as_dict()
    assert "SIGTERM" in result.signals_sent


def test_a_reused_pid_is_never_signalled():
    """If the leased PID now carries a different start key, it belongs to another process: leave it."""

    # quit does nothing; the PID stays alive but its key has changed (the kernel reused it).
    world = World({4321: "someone-elses-key"})
    driver = FakeDriver(pid=4321, quit_behavior="return")
    lease = make_lease(driver, world, start_key="gen-1")
    result = lease.retire()
    assert world.signals == [], "signalled a PID the lease could not prove was its own"
    # Our generation's process is gone (the key no longer matches), so retirement is proven complete.
    assert result.proven_gone is True, result.as_dict()
    assert result.signals_sent == []


def test_a_reparented_or_absent_descendant_is_proven_gone_without_a_signal():
    world = World({})  # the leased PID is not alive at all
    driver = FakeDriver(pid=4321, quit_behavior="return")
    lease = make_lease(driver, world, start_key="gen-1")
    result = lease.retire()
    assert world.signals == []
    assert result.proven_gone is True and result.signals_sent == [], result.as_dict()


def test_permission_denial_is_recorded_and_not_swallowed():
    world = World({4321: "gen-1"})

    def deny(pid, sig):
        world.signals.append((pid, sig))
        raise PermissionError(1, "Operation not permitted")

    driver = FakeDriver(pid=4321, quit_behavior="return")
    lease = make_lease(driver, world, signal_fn=deny)
    result = lease.retire()
    assert any("SIGTERM" in err and "PermissionError" in err for err in result.errors), result.as_dict()
    # It could not prove the process gone, and it never pretends it did.
    assert result.proven_gone is False, result.as_dict()


def test_escalates_to_kill_only_when_term_did_not_clear_the_process():
    world = World({4321: "gen-1"})

    def term_ignored(pid, sig):
        world.signals.append((pid, sig))
        if sig == signal.SIGKILL:
            world.alive.pop(pid, None)  # only KILL clears this stubborn process

    driver = FakeDriver(pid=4321, quit_behavior="return")
    lease = make_lease(driver, world, signal_fn=term_ignored)
    result = lease.retire()
    assert result.signals_sent == ["SIGTERM", "SIGKILL"], result.as_dict()
    assert result.proven_gone is True, result.as_dict()


def test_retire_all_cleans_every_driver_and_aggregates():
    world = World({11: "a", 22: "b"})

    def quit_and_die_factory(pid):
        driver = FakeDriver(pid=pid)

        def q():
            world.alive.pop(pid, None)

        driver.quit = q
        return driver

    lease_a = make_lease(quit_and_die_factory(11), world, pid=11, start_key="a")
    lease_b = make_lease(quit_and_die_factory(22), world, pid=22, start_key="b")
    aggregate = retire_all([lease_a, lease_b])
    assert aggregate["proven_gone"] is True, aggregate
    assert len(aggregate["results"]) == 2 and aggregate["errors"] == []

    # One lease whose retire() raises must not strand the other's cleanup.
    class Exploding(WebDriverLease):
        def retire(self):
            raise RuntimeError("boom")

    exploding = Exploding(FakeDriver(pid=33), generation=DriverGeneration(33, "c"), identity_fn=world.identity, signal_fn=world.signal)
    lease_c = make_lease(quit_and_die_factory(11), World({11: "a"}), pid=11, start_key="a")
    mixed = retire_all([exploding, lease_c])
    assert mixed["proven_gone"] is False and mixed["errors"], mixed
    assert len(mixed["results"]) == 1, mixed
