#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Small pytest collection plugin used to validate the canonical lane catalog."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable

import pytest

from tools.test_catalog import DEPRECATED_PYTHON_MARKER, wholly_deprecated


def collection_rows(items: Iterable[object]) -> list[dict[str, object]]:
    """Project an ordered pytest collection into its stable semantic ledger."""

    return [
        {
            "nodeid": item.nodeid,
            "markers": sorted(marker.name for marker in item.iter_markers()),
        }
        for item in items
    ]


def pytest_addoption(parser):
    parser.addoption("--run-deprecated-python", action="store_true", default=False,
                     help="admit legacy Python backend and coexistence tests for explicit maintenance")
    parser.addoption("--yolomux-catalog-output", action="store", default="", help="write collected node IDs and marks to this JSON file")


def pytest_collection_finish(session):
    destination = session.config.getoption("yolomux_catalog_output")
    if not destination:
        return
    rows = collection_rows(session.items)
    Path(destination).write_text(json.dumps(rows, sort_keys=True), encoding="utf-8")


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("run_deprecated_python"):
        return
    excluded = [item for item in items if item.get_closest_marker(DEPRECATED_PYTHON_MARKER) is not None]
    if excluded:
        items[:] = [item for item in items if item not in excluded]
        config.hook.pytest_deselected(items=excluded)



def pytest_configure(config: pytest.Config) -> None:
    os.environ["YOMUX_RUN_DEPRECATED_PYTHON"] = "1" if config.getoption("run_deprecated_python") else "0"
    # Explicit file/node arguments bypass pytest_ignore_collect. Filter them before
    # collection too, so opting out never imports a retired implementation.
    if not config.getoption("run_deprecated_python"):
        config.args[:] = [argument for argument in config.args
                          if not _deprecated_argument(argument, config.rootpath)]


def _deprecated_argument(argument: str, repo_root: Path) -> bool:
    path = Path(argument.split("::", 1)[0])
    if not path.is_absolute():
        path = Path.cwd() / path
    return (path.is_file() and path.name.startswith("test_") and path.suffix == ".py"
            and wholly_deprecated(path, repo_root=repo_root))


@pytest.hookimpl(tryfirst=True)
def pytest_ignore_collect(collection_path: Path, config: pytest.Config) -> bool | None:
    if (not config.getoption("run_deprecated_python") and collection_path.suffix == ".py"
            and collection_path.name.startswith("test_") and wholly_deprecated(collection_path, repo_root=config.rootpath)):
        return True
    return None
