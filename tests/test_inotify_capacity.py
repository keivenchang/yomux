# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Admission of this host's inotify capacity before the heavy gate lanes start."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests import gate_harness
from tools import check as check_module
from tools.check import Lane
from tools.check import Step
from tools.check import admit_inotify_capacity
from tools.check import heavy_lane_names
from yolomux_lib.infra import inotify_capacity
from yolomux_lib.infra.inotify_capacity import CODE_HEADROOM_BELOW
from yolomux_lib.infra.inotify_capacity import CODE_INSTANCES_BELOW
from yolomux_lib.infra.inotify_capacity import CODE_QUEUED_BELOW
from yolomux_lib.infra.inotify_capacity import CODE_UNMEASURABLE
from yolomux_lib.infra.inotify_capacity import CODE_WATCHES_BELOW
from yolomux_lib.infra.inotify_capacity import REQUIRED_MAX_QUEUED_EVENTS
from yolomux_lib.infra.inotify_capacity import REQUIRED_MAX_USER_INSTANCES
from yolomux_lib.infra.inotify_capacity import REQUIRED_MAX_USER_WATCHES
from yolomux_lib.infra.inotify_capacity import UNMEASURED_LIMIT
from yolomux_lib.infra.inotify_capacity import inotify_capacity_verdict


def _limits(monkeypatch, *, instances, watches, queued, in_use):
    values = {
        inotify_capacity.INOTIFY_MAX_USER_INSTANCES_PATH: instances,
        inotify_capacity.INOTIFY_MAX_USER_WATCHES_PATH: watches,
        inotify_capacity.INOTIFY_MAX_QUEUED_EVENTS_PATH: queued,
    }
    monkeypatch.setattr(inotify_capacity, "read_kernel_limit", lambda path: values[path])
    monkeypatch.setattr(inotify_capacity, "inotify_instance_census", lambda: (in_use, {1: in_use}))


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
    refused = admit_inotify_capacity([_lane("py-compile"), _lane("pytest-browser")])
    assert refused is not None and refused.admitted is False


def test_capacity_is_admitted_before_any_lane_runs(monkeypatch):
    """The refusal must precede lane execution, not follow it."""

    _limits(monkeypatch, instances=128, watches=1_048_576, queued=16_384, in_use=57)
    started: list[str] = []
    monkeypatch.setattr(check_module, "run_parallel", lambda selected: started.append("parallel") or [])
    monkeypatch.setattr(check_module, "run_serial", lambda selected: started.append("serial") or [])

    verdict = admit_inotify_capacity([_lane("pytest-browser")])

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
