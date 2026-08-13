# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""F8 test-harness regressions: flock-before-lease ordering, wrapper reaping, baseline receipts.

Every test here owns one measured F8 defect:
  1. A run queued behind another agent's docker launch must wait on the expensive-tool flock while
     holding NO worktree writer lease.
  2. An interrupted container run must reap the docker wrapper AND its container, not leave them
     detached and holding the lock invisible to a name-pattern kill.
  3. Fixture quiescence must wait on an in-flight full watch-diff baseline as a completion receipt
     rather than time out, while still failing closed on genuinely stuck work.
"""

from contextlib import contextmanager
import os
from pathlib import Path
import signal
import sys
import textwrap
import threading
import time

import pytest

from tests import gate_harness as gate_harness_module
from tests.browser_helpers.browser_layout import browser  # noqa: F401
from tests.gate_harness import gate_http_port  # noqa: F401
from tests.gate_harness import gate_live_server  # noqa: F401
from tests.gate_harness import gate_runtime_paths  # noqa: F401
from tests.gate_harness import gate_tmux  # noqa: F401
from tests.gate_harness import load_gate_browser
from tests.gate_harness import wait_for_fixture_api_quiescence
from tests.helpers.browser_contracts import clean_browser_receipt_barrier
from tools.tool_guard import hold_host_tool_flock
from tools.tool_guard import run_reaped_container_command
from tools.tool_guard import TOOL_LOCK_OWNER_ENV
from yolomux_lib.infra.host_identity import current_host_identity
from yolomux_lib.infra.worktree_writer import acquire_worktree_writer
from yolomux_lib.infra.worktree_writer import inspect_worktree_writer
from yolomux_lib.infra.worktree_writer import WRITER_TOKEN_ENV

REPO_ROOT = Path(__file__).resolve().parent.parent


def _root_conftest(pluginmanager):
    """Return the live root conftest plugin (the one owning pytest_cmdline_main).

    A plain ``import conftest`` is ambiguous because tests/conftest.py shares the name and, being
    imported later under the same module name, overwrites sys.modules['conftest']. Importing by file
    path would re-run the root conftest's module-level isolation setup. The live root conftest object
    is retained by pytest's plugin manager, so resolve it there by file identity; monkeypatching it
    affects the exact module pytest runs.
    """

    target = (REPO_ROOT / "conftest.py").resolve()
    for plugin in pluginmanager.get_plugins():
        module_file = getattr(plugin, "__file__", None)
        if module_file and Path(module_file).resolve() == target and hasattr(plugin, "pytest_cmdline_main"):
            return plugin
    raise RuntimeError("root conftest with pytest_cmdline_main is not registered")


# ---------------------------------------------------------------------------
# Defect 1: acquire the tool flock BEFORE the worktree writer lease.
# ---------------------------------------------------------------------------


def test_queued_run_holds_no_writer_lease_while_it_waits_on_the_tool_flock(tmp_path):
    lock_path = tmp_path / "expensive-tools.lock"
    slot_dir = tmp_path / "writer-slot"
    identity = current_host_identity()

    holder_env: dict[str, str] = {}
    holder_ready = threading.Event()
    release_holder = threading.Event()

    def hold_first_run():
        # Simulate a first pytest already inside its container launch, holding the tool flock.
        with hold_host_tool_flock(lock_path, environ=holder_env):
            holder_ready.set()
            release_holder.wait(10)

    first = threading.Thread(target=hold_first_run, name="f8-holder")
    first.start()
    assert holder_ready.wait(10)

    queued_env: dict[str, str] = {}
    lease_acquired = threading.Event()
    release_queued = threading.Event()
    errors: list[BaseException] = []

    def queued_run():
        # The exact conftest order: flock first (this blocks), then the writer lease.
        try:
            with hold_host_tool_flock(lock_path, environ=queued_env):
                with acquire_worktree_writer(tmp_path, purpose="pytest", slot_dir=slot_dir):
                    lease_acquired.set()
                    release_queued.wait(10)
        except BaseException as error:  # surface any failure to the test thread
            errors.append(error)

    second = threading.Thread(target=queued_run, name="f8-queued")
    second.start()

    # While the first run holds the flock, the queued run must be blocked ON the flock and must hold
    # NO writer lease. This is the whole point of F8: a queued run holds nothing while it waits.
    time.sleep(0.4)
    assert not lease_acquired.is_set(), "queued run acquired the writer lease before the tool flock"
    blocked_status = inspect_worktree_writer(tmp_path, host_identity=identity, slot_dir=slot_dir)
    assert not blocked_status.active, f"queued run held a writer lease while waiting on the flock: {blocked_status}"

    # Release the first run; the queued run now wins the flock and only then takes the lease.
    release_holder.set()
    first.join(10)
    assert lease_acquired.wait(10), "queued run never acquired the writer lease after the flock freed"
    active_status = inspect_worktree_writer(tmp_path, host_identity=identity, slot_dir=slot_dir)
    assert active_status.active, f"queued run did not hold the writer lease once running: {active_status}"

    release_queued.set()
    second.join(10)
    assert not errors, f"queued run raised: {errors}"
    assert queued_env.get(TOOL_LOCK_OWNER_ENV) is None, "queued run leaked tool-lock ownership env"


class _StubConfig:
    def __init__(self):
        self.invocation_params = type("Params", (), {"args": []})()
        self.option = type("Option", (), {"collectonly": False})()


def test_pytest_cmdline_main_takes_the_flock_before_the_writer_lease(monkeypatch, pytestconfig):
    # The caller wiring is the actual F8 fix. Exercise pytest_cmdline_main directly so that reverting
    # its order (lease before flock) turns this test red; the manual-nesting concurrency test above
    # cannot catch a caller regression because it never calls the caller.
    events: list[str] = []
    flock_entered = threading.Event()
    flock_release = threading.Event()
    lease_active = {"value": False}
    seen = {"flock_environ": None, "lease_environ": None, "runner_env": None}
    sentinel_token = "sentinel-writer-token-f8"

    @contextmanager
    def fake_flock(lock_path, *, environ, blocking=True):
        seen["flock_environ"] = environ
        events.append("flock-enter")
        flock_entered.set()
        flock_release.wait(10)  # stand in for waiting on a contended tool flock
        try:
            yield 3
        finally:
            events.append("flock-exit")

    @contextmanager
    def fake_lease(root, *, purpose, environ):
        assert purpose == "pytest"
        seen["lease_environ"] = environ
        # The real lease writes the writer token into this exact env so run-tests.sh forwards it.
        environ[WRITER_TOKEN_ENV] = sentinel_token
        events.append("lease-enter")
        lease_active["value"] = True
        try:
            yield object()
        finally:
            lease_active["value"] = False
            events.append("lease-exit")

    def fake_runner(command, *, cwd, env):
        events.append("runner")
        seen["runner_env"] = env
        return 0

    root_conftest = _root_conftest(pytestconfig.pluginmanager)
    monkeypatch.setattr(root_conftest.docker_image, "container_available", lambda _root: (True, "test"))
    monkeypatch.setattr(root_conftest, "parent_owns_tool_lock", lambda *args, **kwargs: False)
    monkeypatch.setattr(root_conftest, "hold_host_tool_flock", fake_flock)
    monkeypatch.setattr(root_conftest.worktree_writer, "acquire_worktree_writer", fake_lease)
    monkeypatch.setattr(root_conftest, "run_reaped_container_command", fake_runner)

    result = {}
    caller = threading.Thread(
        target=lambda: result.update(rc=root_conftest.pytest_cmdline_main(_StubConfig())),
        name="f8-cmdline",
    )
    caller.start()

    # While the caller is blocked acquiring the flock, it must NOT have taken the writer lease.
    assert flock_entered.wait(10)
    time.sleep(0.2)
    assert lease_active["value"] is False, "writer lease was taken before the tool flock was held"
    assert "lease-enter" not in events

    flock_release.set()
    caller.join(10)

    assert result.get("rc") == 0
    # The exact nesting: flock outermost, then lease, then the container runner.
    assert events == ["flock-enter", "lease-enter", "runner", "lease-exit", "flock-exit"]
    # Token forwarding: the flock, the lease, and the container runner all share ONE env mapping, and
    # the token the lease writes is exactly what the container receives — without this the in-container
    # pytest declares a second, conflicting writer and refuses itself.
    assert seen["lease_environ"] is seen["runner_env"], "lease and runner did not share one env object"
    assert seen["flock_environ"] is seen["runner_env"], "flock and runner did not share one env object"
    assert seen["runner_env"][WRITER_TOKEN_ENV] == sentinel_token


# ---------------------------------------------------------------------------
# Defect 2: an interrupted container run reaps the wrapper and its container.
# ---------------------------------------------------------------------------


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def test_interrupted_container_run_reaps_the_wrapper_and_its_container(tmp_path):
    grandchild_file = tmp_path / "container.pid"
    # The outer python stands in for the docker wrapper; the `sleep` grandchild it spawns in the same
    # process group stands in for the --rm container. A name-pattern kill of the inner process left
    # exactly such a grandchild alive holding the lock; reaping the session must remove it.
    command = [
        sys.executable,
        "-c",
        textwrap.dedent(
            f"""
            import subprocess, sys, time
            container = subprocess.Popen(['sleep', '300'])
            with open({str(grandchild_file)!r}, 'w') as handle:
                handle.write(str(container.pid))
            time.sleep(300)
            """
        ),
    ]

    def fire_interrupt():
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            if grandchild_file.exists() and grandchild_file.read_text().strip():
                break
            time.sleep(0.02)
        time.sleep(0.1)
        os.kill(os.getpid(), signal.SIGINT)  # delivered to the main thread blocked in wait()

    interrupter = threading.Thread(target=fire_interrupt, name="f8-interrupt")
    interrupter.start()
    with pytest.raises(KeyboardInterrupt):
        run_reaped_container_command(command, cwd=tmp_path, env=os.environ)
    interrupter.join(10)

    grandchild_pid = int(grandchild_file.read_text().strip())
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and _pid_alive(grandchild_pid):
        time.sleep(0.02)
    assert not _pid_alive(grandchild_pid), (
        f"container wrapper grandchild {grandchild_pid} survived the interrupted run"
    )


# ---------------------------------------------------------------------------
# Defect 3: wait on the in-flight full watch-diff baseline as a completion receipt.
# ---------------------------------------------------------------------------


# The full watch-diff baseline parks its own operation record in apiOperationState.pending while it
# awaits the 202 result, and marks it terminalOwner='filesystem-watch-diff-refresh'. The lifecycle
# adapter surfaces that as watchDiffPendingOperationIds, so the teardown state that r1 hit carries the
# baseline's own ID in `pending` AND in the baseline-owned set.
_BASELINE_OP_ID = "op-2207ab-baseline"


def _blocked_by_baseline_state():
    return {
        "available": True,
        "diagnosticMode": "retained-js",
        "pending": [_BASELINE_OP_ID],
        "watchDiffPendingOperationIds": [_BASELINE_OP_ID],
        "batchQueued": 0,
        "batchPending": 0,
        "batchOperations": 0,
        "activityRefreshing": False,
        "watchRootsPending": True,
        "watchRootsTimerPending": False,
        "watchRootsRegistrationPending": False,
        "watchRootsInFlight": False,
        "watchRootsBaselinePending": True,
        "finderWatchReady": True,
    }


def _quiescent_state():
    state = _blocked_by_baseline_state()
    # The terminal receipt clears BOTH the baseline's parked pending op and the in-flight promise flag.
    state["pending"] = []
    state["watchDiffPendingOperationIds"] = []
    state["watchRootsPending"] = False
    state["watchRootsBaselinePending"] = False
    return state


def test_quiescence_waits_out_the_in_flight_watch_diff_baseline_receipt():
    class BaselineReceiptDriver:
        def __init__(self):
            self.baseline_awaited = 0
            self.receipt_scripts = []

        def execute_script(self, _script):
            # Stays blocked-only-by-baseline until the baseline receipt has been awaited once.
            return _quiescent_state() if self.baseline_awaited else _blocked_by_baseline_state()

        def execute_async_script(self, script, *args):
            if "watchDiffPromise" in script:
                self.baseline_awaited += 1
                assert args and int(args[0]) > 0
                return {"hadPromise": True, "settled": True, "rejected": False}
            self.receipt_scripts.append(script)
            return clean_browser_receipt_barrier(accepted=1)

    driver = BaselineReceiptDriver()
    settled = wait_for_fixture_api_quiescence(driver, timeout=0.05)

    # The gate must OPEN even though the baseline parks its own ID in pending, enter the receipt wait,
    # and reach quiescence once the terminal receipt clears both the pending op and the promise flag.
    assert driver.baseline_awaited == 1, "the in-flight baseline receipt was not awaited"
    assert settled["watchDiffBaselineReceipt"] == {"hadPromise": True, "settled": True, "rejected": False}
    assert settled["browserReceiptBarrier"] == clean_browser_receipt_barrier(accepted=1)
    assert settled["pending"] == []
    assert settled["watchDiffPendingOperationIds"] == []
    assert settled["watchRootsBaselinePending"] is False


def test_quiescence_fails_closed_when_an_unrelated_op_is_pending_beside_the_baseline():
    class UnrelatedPendingDriver:
        def execute_script(self, _script):
            state = _blocked_by_baseline_state()
            # One unrelated operation is pending alongside the baseline's own parked op. Only the
            # baseline ID is baseline-owned, so the unrelated op must keep the gate closed.
            state["pending"] = sorted([_BASELINE_OP_ID, "op-unrelated-journey"])
            state["watchDiffPendingOperationIds"] = [_BASELINE_OP_ID]
            return state

        def execute_async_script(self, *_args):  # pragma: no cover - must never run
            raise AssertionError("unrelated pending work must never enter the baseline receipt wait")

    with pytest.raises(AssertionError, match="did not quiesce before the owned boundary"):
        wait_for_fixture_api_quiescence(UnrelatedPendingDriver(), timeout=0.01)


def test_quiescence_fails_closed_on_a_stuck_watch_registration_not_a_baseline():
    class StuckRegistrationDriver:
        def execute_script(self, _script):
            state = _blocked_by_baseline_state()
            # In-flight registration is ordinary pending work, not a baseline completion receipt.
            state["watchRootsInFlight"] = True
            state["watchRootsBaselinePending"] = False
            return state

        def execute_async_script(self, *_args):  # pragma: no cover - must never run
            raise AssertionError("a stuck registration must not be waited out as a baseline receipt")

    with pytest.raises(AssertionError, match="did not quiesce before the owned boundary"):
        wait_for_fixture_api_quiescence(StuckRegistrationDriver(), timeout=0.01)


def test_quiescence_fails_closed_when_the_baseline_promise_stays_in_flight(monkeypatch):
    monkeypatch.setattr(gate_harness_module, "_WATCH_DIFF_BASELINE_RECEIPT_SECONDS", 0.2)

    class InFlightBaselineDriver:
        def __init__(self):
            self.baseline_awaited = 0

        def execute_script(self, _script):
            return _blocked_by_baseline_state()

        def execute_async_script(self, script, *args):
            assert "watchDiffPromise" in script
            self.baseline_awaited += 1
            # A genuinely in-flight promise blocks for the whole requested slice, exactly as the real
            # browser wait does. This is what consumes the bound; the wait must not spin.
            time.sleep(int(args[0]) / 1000.0)
            return {"hadPromise": True, "settled": False, "timedOut": True}

    driver = InFlightBaselineDriver()
    started = time.monotonic()
    with pytest.raises(AssertionError, match="did not deliver its completion receipt before the fail-closed bound"):
        wait_for_fixture_api_quiescence(driver, timeout=0.05)
    elapsed = time.monotonic() - started
    # The wait is bounded by real in-flight time, not an instant CPU spin: the genuinely in-flight
    # promise blocked for the bound before the fail-closed limit tripped.
    assert 0.15 <= elapsed < 3.0, elapsed
    assert driver.baseline_awaited >= 1


def test_quiescence_fails_immediately_when_baseline_pending_has_no_in_flight_promise(monkeypatch):
    monkeypatch.setattr(gate_harness_module, "_WATCH_DIFF_BASELINE_RECEIPT_SECONDS", 5.0)

    class MissingPromiseDriver:
        def __init__(self):
            self.baseline_awaited = 0

        def execute_script(self, _script):
            return _blocked_by_baseline_state()

        def execute_async_script(self, script, *_args):
            assert "watchDiffPromise" in script
            self.baseline_awaited += 1
            return {"hadPromise": False, "settled": True}

    driver = MissingPromiseDriver()
    started = time.monotonic()
    with pytest.raises(AssertionError, match="reported pending with no in-flight promise to await"):
        wait_for_fixture_api_quiescence(driver, timeout=0.05)
    # It must fail on the contradiction, not spin the multi-second bound.
    assert time.monotonic() - started < 2.0
    assert driver.baseline_awaited == 1


def test_quiescence_fails_when_receipt_settles_but_state_stays_pending(monkeypatch):
    monkeypatch.setattr(gate_harness_module, "_WATCH_DIFF_BASELINE_RECEIPT_SECONDS", 5.0)

    class RejectedButStillPendingDriver:
        def __init__(self):
            self.baseline_awaited = 0

        def execute_script(self, _script):
            # The baseline flag stays stuck on even after the promise rejected.
            return _blocked_by_baseline_state()

        def execute_async_script(self, script, *_args):
            assert "watchDiffPromise" in script
            self.baseline_awaited += 1
            return {"hadPromise": True, "settled": True, "rejected": True}

    driver = RejectedButStillPendingDriver()
    started = time.monotonic()
    with pytest.raises(AssertionError, match="settled but fixture work is still not quiescent"):
        wait_for_fixture_api_quiescence(driver, timeout=0.05)
    # A settled/rejected receipt triggers exactly one re-read then a raise; no spin on the bound.
    assert time.monotonic() - started < 2.0
    assert driver.baseline_awaited == 1


# ---------------------------------------------------------------------------
# Defect 3: ownership-field validation contract.
# ---------------------------------------------------------------------------


def test_read_state_requires_the_ownership_field_for_retained_js():
    class MissingOwnershipDriver:
        def execute_script(self, _script):
            state = _blocked_by_baseline_state()
            del state["watchDiffPendingOperationIds"]
            return state

        def execute_async_script(self, *_args):  # pragma: no cover - must never run
            raise AssertionError("a missing ownership field must fail closed before any receipt wait")

    with pytest.raises(AssertionError, match="watch-diff pending ownership is missing"):
        wait_for_fixture_api_quiescence(MissingOwnershipDriver(), timeout=0.01)


def test_read_state_rejects_an_owned_id_absent_from_pending():
    class ContradictoryOwnershipDriver:
        def execute_script(self, _script):
            state = _blocked_by_baseline_state()
            # operationState() builds both lists from one Map, so an owned ID not in pending is a
            # malformed contradiction, not a race.
            state["watchDiffPendingOperationIds"] = sorted([_BASELINE_OP_ID, "op-phantom-owned"])
            return state

        def execute_async_script(self, *_args):  # pragma: no cover - must never run
            raise AssertionError("a non-subset ownership set must fail closed before any receipt wait")

    with pytest.raises(AssertionError, match="watch-diff pending ownership is not a subset of pending"):
        wait_for_fixture_api_quiescence(ContradictoryOwnershipDriver(), timeout=0.01)


def test_read_state_rejects_a_malformed_ownership_field_in_browser_console_mode():
    class MalformedOwnershipDriver:
        def execute_script(self, _script):
            # Browser-console may omit the retained-JS-only field, but a present malformed value must
            # still fail closed.
            return {
                "available": True,
                "diagnosticMode": "browser-console",
                "pending": [],
                "watchDiffPendingOperationIds": [123],
            }

    with pytest.raises(AssertionError, match="watch-diff pending ownership is malformed"):
        wait_for_fixture_api_quiescence(MalformedOwnershipDriver(), timeout=0.01)


@pytest.mark.browser
def test_generated_lifecycle_adapter_partitions_pending_by_terminal_owner(browser, gate_live_server):
    # Behavioral proof that the REAL generated adapter derives watchDiffPendingOperationIds from each
    # record's terminalOwner. The Python fakes above hand-construct the field; only this exercises the
    # actual operationState() partition logic in the shipped bundle. Inject one baseline-owned record
    # (terminalOwner='filesystem-watch-diff-refresh') and one unrelated pending record, read the
    # partition, then remove both so teardown quiescence is unaffected.
    load_gate_browser(browser, gate_live_server)
    partition = browser.execute_script(
        """
        const baselineId = 'op-f8-adapter-baseline';
        const unrelatedId = 'op-f8-adapter-unrelated';
        const baselineRecord = {id: baselineId, terminalOwner: 'filesystem-watch-diff-refresh'};
        const unrelatedRecord = {id: unrelatedId};
        apiOperationState.records.set(baselineId, baselineRecord);
        apiOperationState.pending.set(baselineId, baselineRecord);
        apiOperationState.records.set(unrelatedId, unrelatedRecord);
        apiOperationState.pending.set(unrelatedId, unrelatedRecord);
        try {
          const state = window.__yolomuxFixtureLifecycle.operationState();
          return {pending: state.pending, owned: state.watchDiffPendingOperationIds};
        } finally {
          apiOperationState.pending.delete(baselineId);
          apiOperationState.records.delete(baselineId);
          apiOperationState.pending.delete(unrelatedId);
          apiOperationState.records.delete(unrelatedId);
        }
        """
    )
    assert "op-f8-adapter-baseline" in partition["pending"]
    assert "op-f8-adapter-unrelated" in partition["pending"]
    # The baseline op is baseline-owned; the unrelated op is pending but NOT owned. That is the exact
    # partition, and it can only be right if the adapter actually reads terminalOwner.
    assert "op-f8-adapter-baseline" in partition["owned"]
    assert "op-f8-adapter-unrelated" not in partition["owned"]
