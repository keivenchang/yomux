#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Derived pytest file catalogs for the local check lanes.

Pytest only imports files which can contribute to a lane. The catalog is
derived from test definitions and their static pytest markers so adding a test
file cannot silently leave it outside the default gate.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

from tools.test_plan import automatic_test_markers
from tools.test_plan import PHASE_MARKER_PRECEDENCE
from tools.test_plan import phase_for_markers
from tools.test_plan import TEST_PHASE_NAMES
from tools.test_plan import TEST_PHASE_SPECS
from tools.test_plan import test_node_sort_key


REPO_ROOT = Path(__file__).resolve().parent.parent
TEST_ROOT = REPO_ROOT / "tests"
def _pytest_marker_name(node: ast.AST) -> str | None:
    value = node.func if isinstance(node, ast.Call) else node
    if not isinstance(value, ast.Attribute):
        return None
    mark = value.value
    if (
        isinstance(mark, ast.Attribute)
        and mark.attr == "mark"
        and isinstance(mark.value, ast.Name)
        and mark.value.id == "pytest"
    ):
        return value.attr
    return None


def _pytest_markers(node: ast.AST) -> set[str]:
    """Return markers applied by this expression, excluding marker-valued arguments."""

    marker = _pytest_marker_name(node)
    if marker is not None:
        return {marker}
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return set().union(*(_pytest_markers(element) for element in node.elts), set())
    return set()


def _assigned_pytest_markers(body: list[ast.stmt]) -> set[str]:
    markers: set[str] = set()
    for statement in body:
        if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
            continue
        targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
        if not any(isinstance(target, ast.Name) and target.id == "pytestmark" for target in targets):
            continue
        markers.update(_pytest_markers(statement.value))
    return markers


def _decorator_markers(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> set[str]:
    markers: set[str] = set()
    for decorator in node.decorator_list:
        markers.update(_pytest_markers(decorator))
    return markers


def test_definitions(path: Path, *, repo_root: Path = REPO_ROOT) -> tuple[tuple[str, str], ...]:
    """Return static node IDs and phases in pytest's definition order."""

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    automatic = set(automatic_test_markers(path))
    module_markers = _assigned_pytest_markers(tree.body)
    relative = path.relative_to(repo_root).as_posix()
    definitions: list[tuple[str, str]] = []

    def visit(body: list[ast.stmt], inherited: set[str], parents: tuple[str, ...] = ()) -> None:
        for statement in body:
            if isinstance(statement, ast.ClassDef):
                class_markers = inherited | _decorator_markers(statement) | _assigned_pytest_markers(statement.body)
                visit(statement.body, class_markers, (*parents, statement.name))
            elif isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)) and statement.name.startswith("test_"):
                nodeid = "::".join((relative, *parents, statement.name))
                phase = phase_for_markers(automatic | inherited | _decorator_markers(statement))
                definitions.append((nodeid, phase))

    visit(tree.body, module_markers)
    return tuple(definitions)


def file_phases(path: Path) -> set[str]:
    """Return every check lane phase containing a statically defined test in path."""

    return {phase for _nodeid, phase in test_definitions(path)}


def discover_pytest_phase_files(
    test_root: Path = TEST_ROOT,
    *,
    repo_root: Path = REPO_ROOT,
) -> dict[str, tuple[str, ...]]:
    """Derive phase file ownership from every Python test under test_root."""

    definitions: list[tuple[str, str, str]] = []
    for path in sorted(test_root.rglob("test_*.py")):
        relative = path.relative_to(repo_root).as_posix()
        definitions.extend(
            (nodeid, phase, relative)
            for nodeid, phase in test_definitions(path, repo_root=repo_root)
        )
    ordered = sorted(
        enumerate(definitions),
        key=lambda pair: test_node_sort_key(pair[1][0], pair[0]),
    )
    phase_files = {phase: [] for phase in TEST_PHASE_NAMES}
    for _index, (_nodeid, phase, relative) in ordered:
        if relative not in phase_files[phase]:
            phase_files[phase].append(relative)
    return {phase: tuple(paths) for phase, paths in phase_files.items()}


PYTEST_PHASE_FILES: Final[dict[str, tuple[str, ...]]] = discover_pytest_phase_files()
NONBROWSER_FILES: Final[tuple[str, ...]] = PYTEST_PHASE_FILES["nonbrowser"]
BOOT_FILES: Final[tuple[str, ...]] = PYTEST_PHASE_FILES["boot"]
BROWSER_FILES: Final[tuple[str, ...]] = PYTEST_PHASE_FILES["browser"]
GOLDEN_FILES: Final[tuple[str, ...]] = PYTEST_PHASE_FILES["golden"]
E2E_FILES: Final[tuple[str, ...]] = PYTEST_PHASE_FILES["e2e"]
NODE_BRIDGE_FILES: Final[tuple[str, ...]] = PYTEST_PHASE_FILES["node_bridge"]
MOCK_TRANSCRIPT_FILES: Final[tuple[str, ...]] = ("tests/test_mock_transcripts.py",)
NODE_SHARD_LAUNCHER: Final[str] = "tests/layout_url.test.js"
# The one place a Node shard may be kept out of the gate. Every other `tests/*.test.js` is derived
# from disk below, so a new shard joins the gate by existing rather than by being remembered here.
# This list previously enumerated its 14 members by hand and silently omitted three whole shards,
# including tests/share_theme.test.js and its ~2,700 assertions over quick-open, Finder, Differ,
# editor, terminal, and layout: the gate reported green without ever running them.
NODE_LAYOUT_EXCLUDED_FILES: Final[tuple[str, ...]] = (
    # gate_panels asserts on the decorator prose of tests/test_gate_panels.py, whose own
    # xfail(strict=True) markers already fail the pytest-browser lane if those gates start passing.
    # It duplicates that guarantee as text matching and is written to go red when F9 SubsystemSpec
    # lands, so it belongs to the F9 change, not to the standing gate.
    "tests/gate_panels.test.js",
)


def discover_node_layout_files() -> tuple[str, ...]:
    """Return the Node shards the node-layout lane runs, derived from the shard files on disk."""

    excluded = {NODE_SHARD_LAUNCHER, *NODE_LAYOUT_EXCLUDED_FILES}
    return tuple(
        relative
        for relative in sorted(
            path.relative_to(REPO_ROOT).as_posix() for path in TEST_ROOT.glob("*.test.js")
        )
        if relative not in excluded
    )


NODE_LAYOUT_FILES: Final[tuple[str, ...]] = discover_node_layout_files()
# Non-shard JavaScript modules which directly own test scenarios invoked by a
# registered shard. Keeping them explicit makes architecture budgets recursive
# without mistaking generic DOM helpers for assertion owners.
NODE_TEST_HELPER_OWNERS: Final[tuple[str, ...]] = (
    "tests/browser_helpers/editor_preview_suite.js",
)

# Python helper modules which own cohesive assertion families while their thin
# collecting facades retain the historical pytest node IDs. These are explicit
# because pytest collection alone cannot discover a non-test-named owner.
PYTHON_TEST_HELPER_OWNERS: Final[tuple[str, ...]] = (
    "tests/helpers/fixture_http_server.py",
    "tests/subsystems/app_darwin_memory.py",
    "tests/subsystems/browser_harness_lifecycle.py",
    "tests/subsystems/stats_24h_http.py",
)

def focused_phase_target_args(phase: str) -> list[str]:
    """Return catalog-owned focused targets, or the canonical phase files."""

    if phase not in PYTEST_PHASE_FILES:
        raise KeyError(phase)
    spec = next(spec for spec in TEST_PHASE_SPECS if spec.name == phase)
    return list(spec.focused_target_args or PYTEST_PHASE_FILES[phase])


def pytest_files(phase: str) -> list[str]:
    """Return the derived pytest targets for one canonical phase."""

    return list(PYTEST_PHASE_FILES[phase])
