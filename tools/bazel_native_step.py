#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Run an existing gate step against a private copy of its declared inputs."""

import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).absolute().parents[1]
if os.environ.get("TEST_SRCDIR") and len(sys.argv) > 1:
    candidate = Path(os.environ["TEST_SRCDIR"]) / sys.argv[1]
    if candidate.is_dir():
        ROOT = candidate
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.bazel_execution_contract import validate_options
from tools.bazel_generate import copy_runtime_tree


def working_directory(work, descriptor):
    relative = descriptor.get("cwd", ".")
    if not isinstance(relative, str) or Path(relative).is_absolute():
        raise ValueError("native gate working directory must be repository-relative")
    directory = (work / relative).resolve(strict=True)
    if not directory.is_relative_to(work.resolve()) or not directory.is_dir():
        raise ValueError("native gate working directory escapes declared inputs")
    return directory


def main():
    source = Path(os.environ["TEST_SRCDIR"]) / sys.argv[1]
    work = Path(os.environ["TEST_TMPDIR"]) / "workspace"
    copy_runtime_tree(source, work, writable=True)
    fixture = work / "tools/native_git_fixture"
    if fixture.is_dir():
        (work / ".git").write_bytes(b"gitdir: tools/native_git_fixture\n")
        for path in fixture.rglob("*"):
            path.chmod(0o555 if path.is_dir() else 0o444)
        fixture.chmod(0o555)
    if len(sys.argv) != 4:
        raise ValueError("native gate step requires its orchestration command descriptor")
    descriptor = json.loads(sys.argv[3])
    if "steps" in descriptor:
        descriptor = descriptor["steps"][sys.argv[2]]
    os.chdir(working_directory(work, descriptor))
    arguments = descriptor["args"]
    if not isinstance(arguments, list) or not arguments or not all(isinstance(value, str) for value in arguments):
        raise ValueError("invalid native gate command")
    environment = dict(os.environ)
    overrides = dict(descriptor["env"])
    validate_options(["--test_env=" + key + "=" + value for key, value in overrides.items()])
    environment.update(overrides)
    if arguments[0] not in {"python3", "node"}:
        raise ValueError("native gate commands must use declared Python or Node tools")
    if arguments[0] == "python3":
        arguments[0] = sys.executable
    environment.update(YOMUX_CHECK_CONTAINER="0", YOLOMUX_CHECK_CONTAINER="0")
    binary = Path(os.environ["TEST_SRCDIR"]) / "_main/cmd/yomux-go/yomux-go_/yomux-go"
    if arguments[:3] == [sys.executable, "-m", "pytest"]:
        if not binary.is_file():
            raise ValueError("declared Go fixture binary is missing")
        environment["YOMUX_GO_BINARY"] = str(binary)
    return subprocess.run(arguments, env=environment).returncode


if __name__ == "__main__":
    sys.exit(main())
