# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Resolve fixture tools before fixtures replace PATH with command doubles."""

import os
from pathlib import Path
import shutil
import shlex
import sys
import tempfile

from tools.instance_isolation import synthetic_test_environment
from tools.product_environment import product_env_names
from tools.tmux_paths import SHORT_TMPDIR_ENV, short_temp_base

# Capture the admitted PATH once; individual fixtures intentionally replace it.
_TOOL_PATH = os.environ.get("PATH", "")


def fixture_environment(request, overrides, *, native=False):
    """Preserve the inherited policy context while allocating private child state."""
    environment = synthetic_test_environment(overrides)
    if native or SHORT_TMPDIR_ENV in environment:
        root = Path(tempfile.mkdtemp(prefix="env-", dir=short_temp_base()))
        request.addfinalizer(lambda: shutil.rmtree(root))
        environment[SHORT_TMPDIR_ENV] = str(root)
    return environment


def fixture_executable(name):
    if name == "python3":
        return sys.executable
    executable = shutil.which(name, path=_TOOL_PATH)
    if executable is None:
        raise ValueError("required fixture executable is missing: " + name)
    return str(Path(executable).absolute())


def fixture_command(name, *arguments):
    return shlex.join([fixture_executable(name), *arguments])


def fixture_script(source):
    """Pin only the interpreter, preserving the script body byte for byte."""
    binary = isinstance(source, bytes)
    text = source.decode() if binary else source
    first, separator, body = text.partition("\n")
    interpreter = first.removeprefix("#!").split()[-1]
    name = Path(interpreter).name
    if not first.startswith("#!") or name not in {"sh", "bash", "python3"}:
        raise ValueError("unsupported fixture interpreter: " + first)
    result = "#!" + fixture_executable(name) + separator + body
    return result.encode() if binary else result


def clear_fixture_settings(monkeypatch, *settings):
    """Let a fixture choose either spelling without an inherited override."""
    for setting in settings:
        for name in product_env_names(setting):
            monkeypatch.delenv(name, raising=False)
