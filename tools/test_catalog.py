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
NODE_LAYOUT_FILES: Final[tuple[str, ...]] = (
    "tests/i18n_structured_message.test.js",
    "tests/i18n_locale_registry.test.js",
    "tests/tmux_wall.test.js",
    "tests/layout_restore.test.js",
    "tests/drop_action_result.test.js",
    "tests/file_surface_menu.test.js",
    "tests/side_panes.test.js",
    "tests/editor_preview_core.test.js",
    "tests/editor_preview_tmux.test.js",
    "tests/editor_preview_settings.test.js",
    "tests/stats_current_ui.test.js",
    "tests/stats_current_panel.test.js",
    "tests/tabber.test.js",
    "tests/layout_async.test.js",
    # gate_panels remains an unimplemented placeholder until its browser harness exists.
)


def pytest_files(phase: str) -> list[str]:
    """Return the derived pytest targets for one canonical phase."""

    return list(PYTEST_PHASE_FILES[phase])
