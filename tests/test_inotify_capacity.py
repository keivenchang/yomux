# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Admission of this host's inotify capacity before the heavy gate lanes start."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
import errno

import pytest

from tests import gate_harness
from tools import check as check_module
from tools.check import Lane
from tools.check import Step
from tools.check import admit_inotify_capacity
from tools.check import heavy_lane_names
from yomux_lib.infra import inotify_capacity
from yomux_lib.infra.inotify_capacity import CODE_HEADROOM_BELOW
from yomux_lib.infra.inotify_capacity import CODE_INSTANCES_BELOW
from yomux_lib.infra.inotify_capacity import CODE_QUEUED_BELOW
from yomux_lib.infra.inotify_capacity import CODE_UNMEASURABLE
from yomux_lib.infra.inotify_capacity import CODE_WATCHES_BELOW
from yomux_lib.infra.inotify_capacity import REQUIRED_MAX_QUEUED_EVENTS
from yomux_lib.infra.inotify_capacity import REQUIRED_MAX_USER_INSTANCES
from yomux_lib.infra.inotify_capacity import REQUIRED_MAX_USER_WATCHES
from yomux_lib.infra.inotify_capacity import UNMEASURED_LIMIT
from yomux_lib.infra.inotify_capacity import inotify_capacity_verdict


def _limits(monkeypatch, *, instances, watches, queued, in_use):
    values = {
        inotify_capacity.INOTIFY_MAX_USER_INSTANCES_PATH: instances,
        inotify_capacity.INOTIFY_MAX_USER_WATCHES_PATH: watches,
        inotify_capacity.INOTIFY_MAX_QUEUED_EVENTS_PATH: queued,
    }
    monkeypatch.setattr(inotify_capacity, "read_kernel_limit", lambda path: values[path])
    monkeypatch.setattr(inotify_capacity, "inotify_instance_census", lambda: (in_use, {1: in_use} if in_use >= 0 else {}))


def test_non_linux_census_is_explicitly_unmeasured_without_reading_proc(monkeypatch):
    monkeypatch.setattr(inotify_capacity, "sys", SimpleNamespace(platform="darwin"), raising=False)
    monkeypatch.setattr(inotify_capacity.os, "listdir", lambda _path: (_ for _ in ()).throw(FileNotFoundError("no proc filesystem")))
    assert inotify_capacity.inotify_instance_census() == (UNMEASURED_LIMIT, {})


def test_census_ignores_a_foreign_uid_after_pid_reuse(monkeypatch):
    pid = 12345
    uid = os.getuid()
    observed = 0
    original_stat = os.stat

    def stat_process(path, *args, **kwargs):
        nonlocal observed
        if str(path) == f"/proc/{pid}":
            observed += 1
            return SimpleNamespace(st_uid=uid if observed == 1 else uid + 1)
        return original_stat(path, *args, **kwargs)

    def list_descriptors(path):
        if str(path) == "/proc":
            return [str(pid)]
        if str(path) == f"/proc/{pid}/fd":
            raise PermissionError("PID changed owner after census admission")
        raise AssertionError(f"unexpected proc path: {path}")

    monkeypatch.setattr(inotify_capacity, "sys", SimpleNamespace(platform="linux"), raising=False)
    monkeypatch.setattr(os, "stat", stat_process)
    monkeypatch.setattr(os, "listdir", list_descriptors)

    assert inotify_capacity.inotify_instance_census() == (0, {})
    assert observed == 2


def test_census_still_rejects_unreadable_same_uid_process(monkeypatch):
    pid = 12345
    uid = os.getuid()
    original_stat = os.stat

    def stat_process(path, *args, **kwargs):
        if str(path) == f"/proc/{pid}":
            return SimpleNamespace(st_uid=uid)
        return original_stat(path, *args, **kwargs)

    def list_descriptors(path):
        if str(path) == "/proc":
            return [str(pid)]
        if str(path) == f"/proc/{pid}/fd":
            raise PermissionError("same-uid descriptor census is unreadable")
        raise AssertionError(f"unexpected proc path: {path}")

    monkeypatch.setattr(inotify_capacity, "sys", SimpleNamespace(platform="linux"), raising=False)
    monkeypatch.setattr(os, "stat", stat_process)
    monkeypatch.setattr(os, "listdir", list_descriptors)

    with pytest.raises(PermissionError, match="same-uid descriptor census is unreadable"):
        inotify_capacity.inotify_instance_census()


def test_measured_gate_profile_at_the_current_ceiling_is_refused(monkeypatch):
    """RED: this box's measured 128-instance ceiling cannot admit the gate profile."""

    _limits(monkeypatch, instances=128, watches=1_048_576, queued=16_384, in_use=57)

    verdict = inotify_capacity_verdict()

    assert verdict.admitted is False
    assert verdict.reason_code == CODE_INSTANCES_BELOW
    assert verdict.measured["max_user_instances"] == 128
    assert verdict.required["max_user_instances"] == REQUIRED_MAX_USER_INSTANCES
    assert verdict.in_use_instances == 57
    assert verdict.free_instances == 71
    text = verdict.refusal_text()
    # A refusal has to be actionable: current value, required value, remedy.
    assert "128" in text and str(REQUIRED_MAX_USER_INSTANCES) in text
    assert "sudo sysctl -w" in text
    assert "operator-owned" in text


def test_declared_acceptable_limit_admits_the_same_profile(monkeypatch):
    """GREEN: at the declared minimums the identical profile is admitted."""

    _limits(
        monkeypatch,
        instances=REQUIRED_MAX_USER_INSTANCES,
        watches=REQUIRED_MAX_USER_WATCHES,
        queued=REQUIRED_MAX_QUEUED_EVENTS,
        in_use=57,
    )

    verdict = inotify_capacity_verdict()

    assert verdict.admitted is True, verdict.refusal_text()
    assert verdict.reason_code == "inotify_capacity_admitted"
    assert verdict.free_instances == REQUIRED_MAX_USER_INSTANCES - 57


def test_a_raised_ceiling_still_refuses_when_ambient_holders_ate_the_headroom(monkeypatch):
    """A high ceiling is not capacity if other processes already consumed it."""

    _limits(
        monkeypatch,
        instances=REQUIRED_MAX_USER_INSTANCES,
        watches=REQUIRED_MAX_USER_WATCHES,
        queued=REQUIRED_MAX_QUEUED_EVENTS,
        in_use=REQUIRED_MAX_USER_INSTANCES - 1,
    )

    verdict = inotify_capacity_verdict()

    assert verdict.admitted is False
    assert verdict.reason_code == CODE_HEADROOM_BELOW
    assert verdict.free_instances == 1


@pytest.mark.parametrize(
    ("instances", "watches", "queued", "expected"),
    (
        (128, REQUIRED_MAX_USER_WATCHES, REQUIRED_MAX_QUEUED_EVENTS, CODE_INSTANCES_BELOW),
        (REQUIRED_MAX_USER_INSTANCES, 8_192, REQUIRED_MAX_QUEUED_EVENTS, CODE_WATCHES_BELOW),
        (REQUIRED_MAX_USER_INSTANCES, REQUIRED_MAX_USER_WATCHES, 16_384, CODE_QUEUED_BELOW),
        (UNMEASURED_LIMIT, REQUIRED_MAX_USER_WATCHES, REQUIRED_MAX_QUEUED_EVENTS, CODE_UNMEASURABLE),
    ),
)
def test_every_capacity_shortfall_has_its_own_machine_readable_reason(monkeypatch, instances, watches, queued, expected):
    _limits(monkeypatch, instances=instances, watches=watches, queued=queued, in_use=0)

    verdict = inotify_capacity_verdict()

    assert verdict.admitted is False
    assert verdict.reason_code == expected
    assert verdict.as_reason()["remediation"].startswith("sudo sysctl -w")


def _lane(name):
    return Lane(name, name, (Step(name, ["true"]),))


def test_only_fanning_out_lanes_are_gated_on_inotify_capacity(monkeypatch):
    """A lane that creates no watcher must not be refused for a watcher limit."""

    _limits(monkeypatch, instances=128, watches=1_048_576, queued=16_384, in_use=57)

    assert heavy_lane_names([_lane("py-compile"), _lane("node-layout")]) == []
    assert admit_inotify_capacity([_lane("py-compile"), _lane("node-layout")]) is None
    assert heavy_lane_names([_lane("py-compile"), _lane("pytest-browser")]) == ["pytest-browser"]
    refused = admit_inotify_capacity([_lane("py-compile"), _lane("pytest-browser")], profile={"uses_inotify_capacity": True})
    assert refused is not None and refused.admitted is False


def test_darwin_heavy_lanes_do_not_probe_linux_inotify_capacity(monkeypatch):
    monkeypatch.setattr(check_module, "inotify_capacity_verdict", lambda: (_ for _ in ()).throw(AssertionError("Darwin read Linux /proc")))
    profile = {"uses_inotify_capacity": False}
    assert admit_inotify_capacity([_lane("pytest-browser")], profile=profile) is None


def test_capacity_is_admitted_before_any_lane_runs(monkeypatch):
    """The refusal must precede lane execution, not follow it."""

    _limits(monkeypatch, instances=128, watches=1_048_576, queued=16_384, in_use=57)
    started: list[str] = []
    monkeypatch.setattr(check_module, "run_parallel", lambda selected: started.append("parallel") or [])
    monkeypatch.setattr(check_module, "run_serial", lambda selected: started.append("serial") or [])

    verdict = admit_inotify_capacity([_lane("pytest-browser")], profile={"uses_inotify_capacity": True})

    assert verdict is not None and verdict.admitted is False
    assert started == []


def test_admitted_capacity_is_not_rendered_as_a_refusal(monkeypatch):
    """An admitted verdict must never print under a REFUSED banner."""

    _limits(
        monkeypatch,
        instances=REQUIRED_MAX_USER_INSTANCES,
        watches=REQUIRED_MAX_USER_WATCHES,
        queued=REQUIRED_MAX_QUEUED_EVENTS,
        in_use=0,
    )

    verdict = inotify_capacity_verdict()

    assert verdict.admitted is True
    assert verdict.refusal_text().startswith("INOTIFY CAPACITY ADMITTED:")
    assert "REFUSED" not in verdict.refusal_text()


def test_a_raised_ceiling_does_not_excuse_a_fixture_leak():
    """Mitigation must not be able to turn a leak green.

    The teardown invariant that names a surviving daemon reads no inotify limit,
    so raising the ceiling cannot silence it.
    """

    source = Path(gate_harness.__file__).read_text(encoding="utf-8")
    marker = "def assert_no_surviving_local_service_daemons"
    body = source[source.index(marker):]
    body = body[: body.index("\n\nclass ")]
    for limit_name in ("max_user_instances", "REQUIRED_MAX_USER_INSTANCES", "free_instances"):
        assert limit_name not in body, limit_name


@pytest.mark.parametrize("boundary", ["proc", "identity", "descriptors", "target"])
@pytest.mark.parametrize("failure", [PermissionError, FileNotFoundError])
def test_capacity_distinguishes_unmeasurable_owners_from_vanished_processes(monkeypatch, boundary, failure):
    monkeypatch.setattr(inotify_capacity, "sys", SimpleNamespace(platform="linux"))
    def fail_at(name, result):
        if boundary == name:
            raise failure("synthetic census failure")
        return result

    monkeypatch.setattr(inotify_capacity, "read_kernel_limit", lambda _path: 2_000_000)
    def unavailable_probe(_required):
        raise OSError("synthetic unavailable capacity probe")
    monkeypatch.setattr(inotify_capacity, "_probe_free_inotify_instances", unavailable_probe)
    original_listdir, original_stat, original_readlink = inotify_capacity.os.listdir, inotify_capacity.os.stat, inotify_capacity.os.readlink

    def list_processes(path):
        if str(path) == "/proc":
            return fail_at("proc", ["123"])
        if str(path) == "/proc/123/fd":
            return fail_at("descriptors", ["4"])
        return original_listdir(path)

    monkeypatch.setattr(inotify_capacity.os, "listdir", list_processes)
    monkeypatch.setattr(inotify_capacity.os, "stat", lambda path, **kwargs: fail_at("identity", SimpleNamespace(st_uid=inotify_capacity.os.getuid())) if str(path) == "/proc/123" else original_stat(path, **kwargs))
    monkeypatch.setattr(inotify_capacity.os, "readlink", lambda path, **kwargs: fail_at("target", inotify_capacity.INOTIFY_FD_TARGET) if str(path) == "/proc/123/fd/4" else original_readlink(path, **kwargs))
    verdict = inotify_capacity_verdict()
    if failure is PermissionError or boundary == "proc":
        assert not verdict.admitted
        assert verdict.reason_code == CODE_UNMEASURABLE
        assert verdict.in_use_instances == verdict.free_instances == UNMEASURED_LIMIT
    else:
        assert verdict.admitted
        assert verdict.in_use_instances == 0


@pytest.mark.parametrize("boundary", ["directory", "descriptor"])
def test_kernel_watch_count_propagates_permission_denial(monkeypatch, boundary):
    def list_descriptors(_path):
        if boundary == "directory":
            raise PermissionError("synthetic fdinfo denial")
        return ["4"]

    def read_descriptor(_path, **_kwargs):
        raise PermissionError("synthetic fdinfo denial")

    monkeypatch.setattr(inotify_capacity.os, "listdir", list_descriptors)
    monkeypatch.setattr(Path, "read_text", read_descriptor)
    with pytest.raises(PermissionError):
        inotify_capacity.inotify_watch_descriptor_count(123)


@pytest.mark.parametrize("available,error_number", [(256, errno.EMFILE), (255, errno.EMFILE), (255, errno.ENFILE), (255, errno.ENOMEM), (0, errno.EMFILE)])
def test_unreadable_census_requires_the_complete_simultaneous_kernel_allowance(monkeypatch, available, error_number):
    _limits(monkeypatch, instances=REQUIRED_MAX_USER_INSTANCES, watches=REQUIRED_MAX_USER_WATCHES, queued=REQUIRED_MAX_QUEUED_EVENTS, in_use=UNMEASURED_LIMIT)
    opened, closed = [], []

    def open_instance():
        if len(opened) == available:
            raise OSError(error_number, "synthetic kernel capacity refusal")
        descriptor = 1_000_000 + len(opened)
        opened.append(descriptor)
        return descriptor

    original_close = inotify_capacity.os.close
    monkeypatch.setattr(inotify_capacity, "_open_inotify_instance", open_instance)
    monkeypatch.setattr(inotify_capacity.os, "close", lambda fd: closed.append(fd) if fd >= 1_000_000 else original_close(fd))
    verdict = inotify_capacity_verdict()
    assert verdict.admitted is (available >= 256)
    assert verdict.in_use_instances == verdict.free_instances == UNMEASURED_LIMIT
    assert closed == opened[::-1]
    if verdict.admitted:
        assert verdict.reason_code == inotify_capacity.ADMITTED_BY_PROBE_CODE
        assert verdict.probed_free_instances == 256
        assert len(opened) == 256
    else:
        assert verdict.reason_code == CODE_UNMEASURABLE
        assert verdict.probed_free_instances == 0
        assert "synthetic kernel capacity refusal" in verdict.measurement_error


def test_direct_probe_does_not_waive_kernel_limit_requirements(monkeypatch):
    _limits(monkeypatch, instances=128, watches=REQUIRED_MAX_USER_WATCHES, queued=REQUIRED_MAX_QUEUED_EVENTS, in_use=UNMEASURED_LIMIT)
    monkeypatch.setattr(inotify_capacity, "_open_inotify_instance", lambda: pytest.fail("probe bypassed insufficient declared limit"))
    assert inotify_capacity_verdict().reason_code == CODE_INSTANCES_BELOW


def test_direct_probe_retires_other_descriptors_when_one_close_fails(monkeypatch):
    opened, closed = [], []

    def open_instance():
        descriptor = 1_000_000 + len(opened)
        opened.append(descriptor)
        return descriptor

    original_close = inotify_capacity.os.close

    def close_instance(descriptor):
        if descriptor < 1_000_000:
            return original_close(descriptor)
        closed.append(descriptor)
        if len(closed) == 1:
            raise OSError("synthetic close failure")

    monkeypatch.setattr(inotify_capacity, "_open_inotify_instance", open_instance)
    monkeypatch.setattr(inotify_capacity.os, "close", close_instance)
    with pytest.raises(OSError, match="synthetic close failure"):
        inotify_capacity._probe_free_inotify_instances(3)
    assert closed == opened[::-1]
