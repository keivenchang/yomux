#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Run the incremental static gate for migrated local-service modules."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
STATIC_SLICE = (
    "yolomux_lib/local_services/protocol_types.py",
    "yolomux_lib/local_services/command_router.py",
    "yolomux_lib/local_services/rpc.py",
    "yolomux_lib/local_services/client.py",
    "yolomux_lib/local_services/registry.py",
    "yolomux_lib/local_services/static_contracts.py",
)


def static_slice() -> tuple[str, ...]:
    """Return the exact migrated-module allowlist; adding a file is an explicit ratchet."""

    return STATIC_SLICE


def main() -> int:
    mypy = shutil.which("mypy")
    if mypy is None:
        print("local-service type gate requires mypy", file=sys.stderr)
        return 2
    # The Docker test mount is deliberately read-only. Keep mypy's SQLite cache in a unique
    # disposable directory so parallel worktrees cannot share stale entries or mutate the source
    # checkout. TemporaryDirectory also removes the cache after this one bounded invocation.
    with tempfile.TemporaryDirectory(prefix="yolomux-mypy-") as cache_dir:
        completed = subprocess.run(
            [mypy, "--cache-dir", cache_dir, "--follow-imports=skip", "--ignore-missing-imports", *static_slice()],
            cwd=REPO_ROOT,
            check=False,
        )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
