# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Establish mock discovery identity before importing the client and renderer."""

import os
from pathlib import Path
import sys


def ensure_mock_process_identity(agent: str) -> None:
    entrypoint = Path(__file__).with_name(f"{agent}.py").resolve()
    if "--mock" not in sys.argv[1:] or Path(sys.argv[0]).resolve() != entrypoint:
        return
    if Path(sys.orig_argv[0]).name == agent:
        return
    # Discovery admits the client identity. Retain interpreter flags across the
    # single re-exec, before either interpreter loads the heavyweight renderer.
    os.execv(sys.executable, [agent, *sys.orig_argv[1:]])
