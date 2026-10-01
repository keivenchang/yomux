# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""statsd resource projection, /livez and /readyz.

Specified in `tools/measurements/statsd-readyz-spec-2026-08-25.md`. The three constraints these
tests exist to hold are: neither endpoint may route through `work_lock`; the projection reports
no PSS or USS field; and the memory budget quantity is `RssAnon + VmSwap`, never `VmRSS`.
"""

from __future__ import annotations

import ast
import pathlib
import threading
from http import HTTPStatus

import pytest

from yomux_lib.stats_current import http
from yomux_lib.stats_current import service as service_module
from yomux_lib.stats_current import storage

PID = 4242
READY_CONTROL = {
    "cache_generation": 7,
    "pending_cells": 0,
    "ring_failure": "",
    "materializer_state": "idle",
    "migration_state": "ready",
    "build_failed_since_publication": False,
    "owed_startup_slots": 0,
}


def _write_proc(root, *, pid=PID, rss_kb=600_000, anon_kb=590_000, swap_kb=1_000_000,
                hwm_kb=1_600_000, state="S", utime=100, stime=50, voluntary=1_000,
                nonvoluntary=10, fds=17, read_bytes=1_000, write_bytes=2_000):
    process = root / str(pid)
    (process / "fd").mkdir(parents=True, exist_ok=True)
    for index in range(fds):
        (process / "fd" / str(index)).write_text("", encoding="utf-8")
    (process / "status").write_text(
        f"Name:\tpython3\nThreads:\t2\nFDSize:\t64\n"
        f"VmPeak:\t1809092 kB\nVmRSS:\t{rss_kb} kB\nRssAnon:\t{anon_kb} kB\n"
        f"RssFile:\t14244 kB\nRssShmem:\t0 kB\nVmSwap:\t{swap_kb} kB\nVmHWM:\t{hwm_kb} kB\n"
        f"voluntary_ctxt_switches:\t{voluntary}\nnonvoluntary_ctxt_switches:\t{nonvoluntary}\n",
        encoding="utf-8",
    )
    # comm carries a space and parentheses on purpose: the parser must split on the LAST ") ".
    fields = ["0"] * 50
    fields[10] = str(utime)  # tail[11], i.e. field 14 overall
    fields[11] = str(stime)  # tail[12], i.e. field 15 overall
    (process / "stat").write_text(
        f"{pid} (py (thon) 3) {state} " + " ".join(fields) + "\n", encoding="utf-8")
    (process / "io").write_text(
        f"rchar: 1\nread_bytes: {read_bytes}\nwrite_bytes: {write_bytes}\n", encoding="utf-8")
    return process


def _sample(tmp_path, **overrides):
    root = tmp_path / "proc"
    _write_proc(root, **overrides)
    return http.read_process_sample(PID, proc_root=root, now=overrides.pop("now", 1_000.0))


def _sizes(tmp_path):
    return http.read_store_sizes(tmp_path / storage.DATABASE_FILENAME, temp_dir=tmp_path)


# --- the constraint the spec exists to protect --------------------------------------------------


def test_the_projection_completes_while_the_worker_holds_work_lock(tmp_path):
    """`_status()` opens `with self.work_lock:` at service.py:4736 and the worker holds it for
    800-940 ms across a build. A health endpoint that waits behind it reports nothing."""

    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock", tmp_path / storage.DATABASE_FILENAME)
    held = threading.Event()
    release = threading.Event()

    def hold_both_locks():
        with service.work_lock, service.cache_lock:
            held.set()
            release.wait(timeout=10.0)

    holder = threading.Thread(target=hold_both_locks, daemon=True)
    holder.start()
    assert held.wait(timeout=5.0), "the holder thread never acquired the locks"
    try:
        done = threading.Event()
        result: list[object] = []

        def project():
            sample = _sample(tmp_path)
            result.append(http.readyz(sample, _sizes(tmp_path), READY_CONTROL))
            done.set()

        worker = threading.Thread(target=project, daemon=True)
        worker.start()
        assert done.wait(timeout=5.0), "the projection blocked while work_lock was held"
    finally:
        release.set()
        holder.join(timeout=5.0)
    assert result and result[0].ok


def test_the_health_module_never_names_a_service_lock():
    """Structural guard: `http.py` must not reach into the daemon's locking at all.

    By AST rather than by grep, because the module comment legitimately explains why
    `work_lock` is excluded. Only a real identifier reference is a violation.
    """

    tree = ast.parse(pathlib.Path(http.__file__).read_text(encoding="utf-8"))
    referenced = {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    } | {
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
    }
    assert "work_lock" not in referenced
    assert "cache_lock" not in referenced
    assert "_status" not in referenced
    # `local_services.client` legitimately contains the word; what must not appear is the
    # daemon module itself, because importing it is how a lock reference gets in.
    imported_names = {
        alias.asname or alias.name
        for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert "service" not in imported_names, imported_names
    modules = {
        node.module for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "yomux_lib.stats_current.service" not in modules, modules


# --- the projection -----------------------------------------------------------------------------


def test_the_projection_reports_no_pss_or_uss_field(tmp_path):
    """`smaps_rollup` is the only source and it takes the target's mmap_read_lock for ~20.8 ms.
    A cheap number wearing an expensive name is worse than an absent one."""

    payload = http.project_resource_state(_sample(tmp_path), _sizes(tmp_path), READY_CONTROL)
    rendered = repr(payload)
    assert "pss_bytes" not in rendered and "uss_bytes" not in rendered
    assert payload["memory"]["pss_available"] is False
    assert payload["memory"]["source"] == "status"


def test_the_memory_budget_is_anon_plus_swap_not_rss(tmp_path):
    """RSS falls when the kernel swaps a process out, so a daemon can breach its budget and
    read healthy. Measured live: VmRSS 61.36% below VmHWM, RssAnon+VmSwap 1.08% below."""

    sample = _sample(tmp_path, rss_kb=600_000, anon_kb=590_000, swap_kb=1_000_000)
    assert sample.rss_bytes == 600_000 * 1024
    assert sample.memory_bytes == (590_000 + 1_000_000) * 1024

    budgets = http.HealthBudgets(memory_bytes=1_000_000 * 1024)
    verdict = http.readyz(sample, _sizes(tmp_path), READY_CONTROL, budgets=budgets)

    assert not verdict.ok, "a budget checked against RSS alone would have passed this"
    assert any("RssAnon+VmSwap" in failure for failure in verdict.failures)


def test_a_process_that_cannot_be_read_fails_closed(tmp_path):
    sample = http.read_process_sample(999_999, proc_root=tmp_path / "proc")
    assert sample.exists is False and sample.error
    verdict = http.readyz(sample, _sizes(tmp_path), READY_CONTROL)
    assert verdict.status == HTTPStatus.SERVICE_UNAVAILABLE
    assert any("not readable" in failure for failure in verdict.failures)


def test_a_comm_containing_spaces_and_parentheses_still_parses(tmp_path):
    sample = _sample(tmp_path, utime=7, stime=11)
    assert sample.state == "S" and sample.cpu_ticks == 18


# --- /readyz ------------------------------------------------------------------------------------


def test_readyz_refuses_while_the_ring_is_still_staged(tmp_path):
    """The readiness gap: on 6 of 6 cold starts `cache_ready_event` fires with 1,248 cells
    staged, and a snapshot at that instant is legitimately refused. A /readyz wired to the
    event inherits the lie."""

    control = READY_CONTROL | {"pending_cells": 1248}
    verdict = http.readyz(_sample(tmp_path), _sizes(tmp_path), control)

    assert not verdict.ok and verdict.status == HTTPStatus.SERVICE_UNAVAILABLE
    assert verdict.retry_after_seconds == 1
    assert any("pending_cells: 1248 staged" in failure for failure in verdict.failures)


def test_readyz_reads_pending_cells_and_not_the_boolean_queue_pending(tmp_path):
    """`status.queue.pending` is `int(bool(...))`, so it reads 1 for one cell or for the whole
    ring. A projection that trusted it could not tell 1 staged cell from 1,248."""

    one = http.readyz(_sample(tmp_path), _sizes(tmp_path), READY_CONTROL | {"pending_cells": 1})
    many = http.readyz(_sample(tmp_path), _sizes(tmp_path), READY_CONTROL | {"pending_cells": 1248})
    assert not one.ok and not many.ok
    assert one.failures != many.failures, "the two are indistinguishable, which is the bug"


@pytest.mark.parametrize("control,expected", [
    ({"cache_generation": 0}, "cache_generation"),
    ({"ring_failure": "disk full"}, "ring_writer.failure"),
    ({"materializer_state": "failed"}, "materializer.state"),
    ({"migration_state": "migrating"}, "migration.state"),
    ({"build_failed_since_publication": True}, "build.failed"),
    ({"owed_startup_slots": 3}, "recovery"),
])
def test_each_readyz_condition_fails_closed(tmp_path, control, expected):
    verdict = http.readyz(_sample(tmp_path), _sizes(tmp_path), READY_CONTROL | control)
    assert not verdict.ok
    assert any(expected in failure for failure in verdict.failures), verdict.failures


def test_readyz_names_every_failing_condition_not_the_first(tmp_path):
    """One cause per poll costs an operator one restart cycle per cause."""

    control = READY_CONTROL | {
        "cache_generation": 0, "pending_cells": 1248,
        "ring_failure": "disk full", "migration_state": "migrating",
    }
    budgets = http.HealthBudgets(memory_bytes=1, open_fds=1)
    verdict = http.readyz(_sample(tmp_path), _sizes(tmp_path), control, budgets=budgets)

    assert len(verdict.failures) == 6, verdict.failures


def test_readyz_without_control_state_is_not_ready(tmp_path):
    """Unknown state is not ready. An unreachable daemon cannot assert its own readiness."""

    verdict = http.readyz(_sample(tmp_path), _sizes(tmp_path), None)
    assert not verdict.ok
    assert any("resource_state unavailable" in failure for failure in verdict.failures)


def test_readyz_passes_only_when_every_condition_holds(tmp_path):
    verdict = http.readyz(
        _sample(tmp_path), _sizes(tmp_path), READY_CONTROL,
        budgets=http.HealthBudgets(memory_bytes=2 * 1024**3, open_fds=48),
    )
    assert verdict.ok and verdict.status == HTTPStatus.OK
    assert verdict.failures == () and verdict.retry_after_seconds is None


# --- /livez -------------------------------------------------------------------------------------


def test_livez_passes_while_a_long_build_burns_cpu(tmp_path):
    """Cold build measured 25-30 s at 1x and 52-56 s at 2x, burning CPU throughout. Busy must
    not read as wedged."""

    first = _sample(tmp_path, utime=100, stime=50)
    second = _sample(tmp_path, utime=900, stime=50, voluntary=1_000)
    verdict = http.livez(second, first, stall_seconds=120.0)
    assert verdict.ok and verdict.status == HTTPStatus.OK


def test_livez_fails_when_cpu_switches_and_io_are_all_flat(tmp_path):
    """The wedge signature: neither computing, nor waiting-and-waking, nor moving bytes."""

    first = _sample(tmp_path)
    later = http.read_process_sample(PID, proc_root=tmp_path / "proc", now=1_500.0)
    verdict = http.livez(later, first, stall_seconds=120.0, has_outstanding_work=True)

    assert not verdict.ok and verdict.status == HTTPStatus.SERVICE_UNAVAILABLE
    assert any("no progress for 500.0s" in failure for failure in verdict.failures)


def test_livez_does_not_call_an_idle_daemon_dead(tmp_path):
    """An idle daemon is flat by definition."""

    first = _sample(tmp_path)
    later = http.read_process_sample(PID, proc_root=tmp_path / "proc", now=1_500.0)
    assert http.livez(later, first, stall_seconds=120.0, has_outstanding_work=False).ok


def test_livez_tolerates_a_flat_window_shorter_than_the_stall_bound(tmp_path):
    first = _sample(tmp_path)
    later = http.read_process_sample(PID, proc_root=tmp_path / "proc", now=1_030.0)
    assert http.livez(later, first, stall_seconds=120.0, has_outstanding_work=True).ok


def test_livez_passes_when_only_context_switches_advance(tmp_path):
    """A process waiting on I/O is not wedged: it is still entering and leaving waits."""

    first = _sample(tmp_path, voluntary=1_000)
    root = tmp_path / "proc2"
    _write_proc(root, voluntary=1_100)
    later = http.read_process_sample(PID, proc_root=root, now=1_500.0)
    assert http.livez(later, first, stall_seconds=120.0, has_outstanding_work=True).ok


@pytest.mark.parametrize("state", ["Z", "T"])
def test_livez_fails_on_a_corpse_or_a_stopped_process(tmp_path, state):
    root = tmp_path / f"proc-{state}"
    _write_proc(root, state=state)
    sample = http.read_process_sample(PID, proc_root=root, now=1_000.0)
    verdict = http.livez(sample, None)
    assert not verdict.ok
    assert any("is not one of R/S/D" in failure for failure in verdict.failures)


def test_livez_fails_closed_when_the_process_is_gone(tmp_path):
    sample = http.read_process_sample(999_999, proc_root=tmp_path / "proc")
    assert not http.livez(sample, None).ok


def test_the_stall_bound_scales_with_the_daemons_own_build_time(tmp_path):
    """A literal 120 s is 4x today's build and 2x a doubled store's. Express it as a multiple."""

    assert http.livez_stall_seconds(None) == 120.0
    assert http.livez_stall_seconds(28.0) == 120.0
    assert http.livez_stall_seconds(55.0) == 220.0
