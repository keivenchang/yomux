# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Current stats daemon revision ownership."""

import ast
from pathlib import Path

from yolomux_lib.stats_current import revision


def test_daemon_revision_covers_every_module_loaded_by_the_writer_process():
    assert revision._CURRENT_MODULES == (
        "browser_family.py",
        "families.py",
        "host_collectors.py",
        "identity.py",
        "materializer.py",
        "migration.py",
        "pricing.py",
        "process_memory.py",
        "protocol.py",
        "resolution.py",
        "revision.py",
        "service.py",
        "storage.py",
        "usage.py",
    )


def test_current_production_path_never_imports_the_legacy_top_level_resolution_module():
    current_root = Path(revision.__file__).resolve().parent
    assert not (current_root.parent / "stats_resolution.py").exists()
    paths = (*current_root.glob("*.py"), current_root.parent / "app.py")
    offenders = []
    for path in paths:
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(
                alias.name == "yolomux_lib.stats_resolution" for alias in node.names
            ):
                offenders.append(path.name)
            if isinstance(node, ast.ImportFrom) and any(
                alias.name == "stats_resolution" for alias in node.names
            ):
                offenders.append(path.name)
    assert offenders == []
