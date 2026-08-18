#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Declarative pytest phase and check-lane ownership.

This module owns names and relationships only. Command construction stays in
``tools.check`` because worker counts and platform policy are runtime inputs.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique
from pathlib import Path
from typing import Collection, Final


@dataclass(frozen=True)
class TestPhaseSpec:
    name: str
    marker: str | None = None
    focused_target_args: tuple[str, ...] = ()


# Order is classification precedence. A test with several phase markers belongs
# to the first matching phase, exactly as the gate did before this registry.
TEST_PHASE_SPECS: Final[tuple[TestPhaseSpec, ...]] = (
    TestPhaseSpec("node_bridge", "node_bridge"),
    TestPhaseSpec("gate_serial", "gate_serial"),
    TestPhaseSpec("e2e", "e2e"),
    TestPhaseSpec("golden", "visual_golden"),
    TestPhaseSpec("boot", "boot"),
    TestPhaseSpec("browser", "browser"),
    TestPhaseSpec("nonbrowser", focused_target_args=("tests", "--ignore=tests/test_browser_layout.py")),
)
TEST_PHASE_NAMES: Final[tuple[str, ...]] = tuple(spec.name for spec in TEST_PHASE_SPECS)
PHASE_MARKER_PRECEDENCE: Final[tuple[tuple[str, str], ...]] = tuple(
    (spec.marker, spec.name)
    for spec in TEST_PHASE_SPECS
    if spec.marker is not None
)

# These nodes are deliberately promoted within pytest collection so xdist can
# start the longest units first. Static phase catalogs consume this same order;
# otherwise a lane's argv and its runtime collection disagree about owner order.
SLOWEST_FIRST_TESTS: Final[tuple[str, ...]] = (
    "tests/test_browser_dockview.py::test_dockview_wrapped_tab_rows_share_one_control_reserved_flex_grid",
    "tests/test_browser_dockview.py::test_differ_reopen_keeps_dragged_file_tab_home",
    "tests/test_browser_layout.py::test_mock_agent_prompt_payload_renders_ask_attention_in_live_browser",
    "tests/test_browser_dockview.py::test_dockview_yellow_window_ball_click_switches_and_acknowledges",
    "tests/test_node_suite.py::test_node_layout_suite_passes",
)
SLOWEST_FIRST_RANK: Final[dict[str, int]] = {
    nodeid: index for index, nodeid in enumerate(SLOWEST_FIRST_TESTS)
}


def test_node_sort_key(nodeid: str, original_index: int) -> tuple[int, int, int]:
    """Return the one stable runtime/static priority for a collected test."""

    base_nodeid = nodeid.split("[", 1)[0]
    rank = SLOWEST_FIRST_RANK.get(nodeid, SLOWEST_FIRST_RANK.get(base_nodeid))
    return (1, original_index, original_index) if rank is None else (0, rank, original_index)


def phase_for_markers(markers: set[str]) -> str:
    return next(
        (spec.name for spec in TEST_PHASE_SPECS if spec.marker is not None and spec.marker in markers),
        "nonbrowser",
    )


def automatic_test_markers(path: str | Path) -> tuple[str, ...]:
    """Return filename-derived markers shared by collection and static cataloging."""

    return ("browser", "socket") if Path(path).name.startswith("test_browser_") else ()


@dataclass(frozen=True)
class LaneSpec:
    name: str
    label: str
    step_ids: tuple["StepId", ...]
    default: bool = False
    prerequisites: tuple[str, ...] = ()
    phases: tuple[str, ...] = ()
    worker_class: str = "serial"
    focused_alias_of: str | None = None
    run_last: bool = False


@unique
class StepId(StrEnum):
    """Total identity shared by lane descriptors and executable steps."""

    PY_COMPILE = "py-compile"
    STATIC_BUILD = "static-build"
    TEXTSHAPE = "textshape"
    ARCHITECTURE_BUDGETS = "architecture-budgets"
    LOCAL_SERVICE_TYPES = "local-service-types"
    NODE_YOLOMUX_SYNTAX = "node-yolomux-syntax"
    NODE_WALL_SYNTAX = "node-wall-syntax"
    NODE_LAYOUT = "node-layout"
    PYTEST_NONBROWSER = "pytest-nonbrowser"
    PYTEST_GATE_SERIAL = "pytest-gate-serial"
    PYTEST_BOOT = "pytest-boot"
    PYTEST_BROWSER = "pytest-browser"
    PYTEST_BROWSER_GOLDEN = "pytest-browser-golden"
    PYTEST_E2E = "pytest-e2e"
    PYTEST_UNIT = "pytest-unit"
    PYTEST_SOCKET = "pytest-socket"
    WHITESPACE = "whitespace"


STEP_PHASES: Final[dict[StepId, tuple[str, ...]]] = {
    StepId.PY_COMPILE: (),
    StepId.STATIC_BUILD: (),
    StepId.TEXTSHAPE: (),
    StepId.ARCHITECTURE_BUDGETS: (),
    StepId.LOCAL_SERVICE_TYPES: (),
    StepId.NODE_YOLOMUX_SYNTAX: (),
    StepId.NODE_WALL_SYNTAX: (),
    StepId.NODE_LAYOUT: (),
    StepId.PYTEST_NONBROWSER: ("nonbrowser",),
    StepId.PYTEST_GATE_SERIAL: ("gate_serial",),
    StepId.PYTEST_BOOT: ("boot",),
    StepId.PYTEST_BROWSER: ("browser",),
    StepId.PYTEST_BROWSER_GOLDEN: ("golden",),
    StepId.PYTEST_E2E: ("e2e",),
    StepId.PYTEST_UNIT: ("nonbrowser",),
    StepId.PYTEST_SOCKET: ("nonbrowser",),
    StepId.WHITESPACE: (),
}


LANE_SPECS: Final[tuple[LaneSpec, ...]] = (
    LaneSpec("py-compile", "py_compile", (StepId.PY_COMPILE,), True),
    LaneSpec("static", "static source checks", (StepId.STATIC_BUILD, StepId.TEXTSHAPE, StepId.ARCHITECTURE_BUDGETS, StepId.LOCAL_SERVICE_TYPES), True),
    LaneSpec("node-syntax", "node syntax", (StepId.NODE_YOLOMUX_SYNTAX, StepId.NODE_WALL_SYNTAX), True),
    LaneSpec("node-layout", "node layout suite", (StepId.NODE_LAYOUT,), True, worker_class="node"),
    LaneSpec("pytest", "pytest non-browser", (StepId.PYTEST_NONBROWSER,), True, phases=("nonbrowser",), worker_class="pytest-xdist"),
    LaneSpec("pytest-boot", "pytest boot smoke", (StepId.PYTEST_BOOT,), phases=("boot",), worker_class="pytest-serial"),
    LaneSpec(
        "pytest-browser",
        "pytest browser",
        (StepId.PYTEST_BROWSER, StepId.PYTEST_BROWSER_GOLDEN),
        True,
        prerequisites=("pytest-boot",),
        phases=("boot", "browser", "golden"),
        worker_class="pytest-mixed",
    ),
    LaneSpec("pytest-e2e", "pytest e2e", (StepId.PYTEST_E2E,), True, phases=("e2e",), worker_class="pytest-xdist"),
    LaneSpec("pytest-gate-serial", "pytest timing-sensitive serial", (StepId.PYTEST_GATE_SERIAL,), True, phases=("gate_serial",), worker_class="pytest-serial", run_last=True),
    LaneSpec("pytest-unit", "pytest unit", (StepId.PYTEST_UNIT,), phases=("nonbrowser",), worker_class="pytest-serial"),
    LaneSpec("pytest-socket", "pytest socket", (StepId.PYTEST_SOCKET,), phases=("nonbrowser",), worker_class="pytest-serial"),
    LaneSpec("whitespace", "git diff --check", (StepId.WHITESPACE,), True),
)
CHECK_LANE_ENV: Final[str] = "YOLOMUX_CHECK_LANE"
PYTEST_LANE_NAMES: Final[tuple[str, ...]] = tuple(spec.name for spec in LANE_SPECS if spec.phases)


def lane_spec(name: str) -> LaneSpec:
    try:
        return next(spec for spec in LANE_SPECS if spec.name == name)
    except StopIteration as error:
        raise KeyError(name) from error


def resolved_lane_step_ids(spec: LaneSpec) -> tuple[StepId, ...]:
    """Resolve prerequisite edges into shared step IDs without copying commands."""

    resolved: list[StepId] = []
    visiting: set[str] = set()

    def add(current: LaneSpec) -> None:
        if current.name in visiting:
            raise ValueError(f"check lane prerequisite cycle at {current.name}")
        visiting.add(current.name)
        for prerequisite in current.prerequisites:
            add(lane_spec(prerequisite))
        visiting.remove(current.name)
        for step_id in current.step_ids:
            if step_id not in resolved:
                resolved.append(step_id)

    add(spec)
    return tuple(resolved)


def validate_lane_specs(available_step_ids: Collection[StepId]) -> None:
    """Fail closed when the declarative plan and executable catalog drift."""

    catalog_ids = tuple(available_step_ids)
    if len(catalog_ids) != len(set(catalog_ids)):
        raise ValueError("duplicate step ID in executable catalog")
    expected_ids = set(StepId)
    actual_ids = set(catalog_ids)
    if missing := sorted(expected_ids - actual_ids, key=str):
        raise ValueError(f"missing executable step IDs: {', '.join(map(str, missing))}")
    if extra := sorted(actual_ids - expected_ids, key=str):
        raise ValueError(f"extra executable step IDs: {', '.join(map(str, extra))}")

    lane_names = [spec.name for spec in LANE_SPECS]
    if len(lane_names) != len(set(lane_names)):
        raise ValueError("duplicate check lane name")
    lane_by_name = {spec.name: spec for spec in LANE_SPECS}
    used_steps: set[StepId] = set()
    direct_step_owners: dict[StepId, str] = {}
    for spec in LANE_SPECS:
        if len(spec.step_ids) != len(set(spec.step_ids)):
            raise ValueError(f"duplicate step ID in lane {spec.name}")
        invalid_steps = [step_id for step_id in spec.step_ids if not isinstance(step_id, StepId)]
        if invalid_steps:
            raise ValueError(f"unknown step ID in lane {spec.name}: {invalid_steps}")
        used_steps.update(spec.step_ids)
        unknown_phases = set(spec.phases) - set(TEST_PHASE_NAMES)
        if unknown_phases:
            raise ValueError(f"unknown test phase in lane {spec.name}: {sorted(unknown_phases)}")
        for prerequisite in spec.prerequisites:
            if prerequisite not in lane_by_name:
                raise ValueError(f"missing prerequisite lane {prerequisite} for {spec.name}")
        if spec.focused_alias_of is not None and spec.focused_alias_of not in lane_by_name:
            raise ValueError(f"missing focused alias owner {spec.focused_alias_of} for {spec.name}")
        if spec.focused_alias_of == spec.name:
            raise ValueError(f"focused alias {spec.name} cannot own itself")
        for step_id in spec.step_ids:
            previous = direct_step_owners.get(step_id)
            if previous is not None and spec.focused_alias_of != previous:
                raise ValueError(
                    f"duplicate step ID {step_id} in lanes {previous} and {spec.name} without focused alias"
                )
            direct_step_owners.setdefault(step_id, spec.name)

    if unused := sorted(expected_ids - used_steps, key=str):
        raise ValueError(f"unreferenced executable step IDs: {', '.join(map(str, unused))}")

    for spec in LANE_SPECS:
        resolved_steps = resolved_lane_step_ids(spec)
        derived_phases = tuple(dict.fromkeys(phase for step_id in resolved_steps for phase in STEP_PHASES[step_id]))
        if derived_phases != spec.phases:
            raise ValueError(
                f"test phase drift in lane {spec.name}: descriptor={spec.phases}, steps={derived_phases}"
            )
        if spec.focused_alias_of is not None:
            owner = lane_by_name[spec.focused_alias_of]
            if owner.focused_alias_of is not None:
                raise ValueError(f"focused alias {spec.name} points to another alias {owner.name}")
            owner_steps = set(resolved_lane_step_ids(owner))
            if not set(resolved_steps) <= owner_steps:
                raise ValueError(f"focused alias {spec.name} has steps outside {spec.focused_alias_of}")
