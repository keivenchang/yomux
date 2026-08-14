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


REPO_ROOT = Path(__file__).resolve().parent.parent
TEST_ROOT = REPO_ROOT / "tests"
PHASE_MARKER_PRECEDENCE: Final[tuple[tuple[str, str], ...]] = (
    ("node_bridge", "node_bridge"),
    ("e2e", "e2e"),
    ("visual_golden", "golden"),
    ("boot", "boot"),
    ("browser", "browser"),
)


def _pytest_markers(node: ast.AST) -> set[str]:
    markers: set[str] = set()
    for child in ast.walk(node):
        if not isinstance(child, ast.Attribute):
            continue
        value = child.value
        if (
            isinstance(value, ast.Attribute)
            and value.attr == "mark"
            and isinstance(value.value, ast.Name)
            and value.value.id == "pytest"
        ):
            markers.add(child.attr)
    return markers


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


def _phase_for_markers(markers: set[str]) -> str:
    return next((phase for marker, phase in PHASE_MARKER_PRECEDENCE if marker in markers), "nonbrowser")


def file_phases(path: Path) -> set[str]:
    """Return every check lane phase containing a statically defined test in path."""

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    automatic = {"browser", "socket"} if path.name.startswith("test_browser_") else set()
    module_markers = _assigned_pytest_markers(tree.body)
    phases: set[str] = set()

    def visit(body: list[ast.stmt], inherited: set[str]) -> None:
        for statement in body:
            if isinstance(statement, ast.ClassDef):
                class_markers = inherited | _decorator_markers(statement) | _assigned_pytest_markers(statement.body)
                visit(statement.body, class_markers)
            elif isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)) and statement.name.startswith("test_"):
                phases.add(_phase_for_markers(automatic | inherited | _decorator_markers(statement)))

    visit(tree.body, module_markers)
    return phases


def discover_pytest_phase_files(
    test_root: Path = TEST_ROOT,
    *,
    repo_root: Path = REPO_ROOT,
) -> dict[str, tuple[str, ...]]:
    """Derive phase file ownership from every Python test under test_root."""

    phase_files = {phase: [] for phase in ("nonbrowser", "boot", "browser", "golden", "e2e", "node_bridge")}
    for path in sorted(test_root.rglob("test_*.py")):
        relative = path.relative_to(repo_root).as_posix()
        for phase in file_phases(path):
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


def pytest_files(phase: str) -> list[str]:
    """Return the derived pytest targets for one canonical phase."""

    return list(PYTEST_PHASE_FILES[phase])
