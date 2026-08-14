#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Small pytest collection plugin used to validate the canonical lane catalog."""

from __future__ import annotations

import json
from pathlib import Path


def pytest_addoption(parser):
    parser.addoption("--yolomux-catalog-output", action="store", default="", help="write collected node IDs and marks to this JSON file")


def pytest_collection_finish(session):
    destination = session.config.getoption("yolomux_catalog_output")
    if not destination:
        return
    rows = [
        {"nodeid": item.nodeid, "markers": sorted(marker.name for marker in item.iter_markers())}
        for item in session.items
    ]
    Path(destination).write_text(json.dumps(rows, sort_keys=True), encoding="utf-8")
