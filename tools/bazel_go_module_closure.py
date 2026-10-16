#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Validate the Go tidy graph offline against declared source and a private seed."""

import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).absolute().parents[1]
if os.environ.get("TEST_SRCDIR") and len(sys.argv) > 1:
    candidate = Path(os.environ["TEST_SRCDIR"]) / sys.argv[1]
    if candidate.is_dir():
        ROOT = candidate
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.bazel_generate import copy_runtime_tree
from tools.docker_image import _download_go_modules
from tools.product_environment import product_env


def prepare_module_cache(source, modules):
    if product_env("BAZEL_EXECUTION") == "native":
        # Diagnostics have no sealed seed; use the shared locked preparation,
        # then run the same offline tidy check as qualified execution.
        modules.mkdir()
        go = shutil.which("go")
        if not go:
            raise ValueError("native module closure requires Go on PATH")
        _download_go_modules(Path(go), source, modules)
    else:
        seed = os.environ.get("GOMODCACHE", "")
        if not seed or not Path(seed).is_absolute():
            raise ValueError("module closure requires an absolute Go module cache seed")
        shutil.copytree(Path(seed), modules)


def main():
    source = Path(os.environ["TEST_SRCDIR"]) / sys.argv[1]
    temporary = Path(os.environ["TEST_TMPDIR"])
    work = temporary / "tidy-source"
    copy_runtime_tree(source, work, writable=True)
    modules = temporary / "go-modules"
    prepare_module_cache(source, modules)
    for directory, _subdirectories, files in os.walk(modules):
        Path(directory).chmod(0o700)
        for name in files:
            path = Path(directory) / name
            path.chmod(0o700 if path.stat().st_mode & 0o111 else 0o600)
    environment = dict(os.environ, GOMODCACHE=str(modules), GOCACHE=str(temporary / "go-cache"),
        GOPATH=str(temporary / "go-path"), GOPROXY="off", GOSUMDB="off", GOENV="off", GOWORK="off", GOTOOLCHAIN="local")
    subprocess.run(["go", "mod", "tidy"], cwd=work, env=environment, check=True)
    if (work / "go.mod").read_bytes() != (source / "go.mod").read_bytes():
        raise ValueError("go.mod differs from the pinned SDK's tidy graph")
    missing = set((work / "go.sum").read_text().splitlines()) - set((source / "go.sum").read_text().splitlines())
    if missing:
        raise ValueError("go.sum lacks required tidy checksums: " + repr(sorted(missing)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
