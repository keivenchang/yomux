# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Cap-the-glibc-arenas default so a hand-launched web process cannot balloon."""
from __future__ import annotations

import os
import sys

from yolomux_lib.infra.malloc_tuning import DEFAULT_ARENA_MAX
from yolomux_lib.infra.malloc_tuning import cap_malloc_arenas


def test_publishes_default_when_no_override(monkeypatch):
    monkeypatch.delenv("MALLOC_ARENA_MAX", raising=False)
    result = cap_malloc_arenas()
    # The env var is published for children/self-restart regardless of platform.
    assert os.environ["MALLOC_ARENA_MAX"] == str(DEFAULT_ARENA_MAX)
    # On this Linux/glibc host mallopt must actually report success.
    if sys.platform.startswith("linux"):
        assert result is True


def test_explicit_env_override_wins(monkeypatch):
    monkeypatch.setenv("MALLOC_ARENA_MAX", "4")
    cap_malloc_arenas(DEFAULT_ARENA_MAX)
    # The operator's explicit value is respected over the argument default.
    assert os.environ["MALLOC_ARENA_MAX"] == "4"


def test_argument_used_when_env_absent(monkeypatch):
    monkeypatch.delenv("MALLOC_ARENA_MAX", raising=False)
    cap_malloc_arenas(3)
    assert os.environ["MALLOC_ARENA_MAX"] == "3"


def test_garbage_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("MALLOC_ARENA_MAX", "not-a-number")
    cap_malloc_arenas()
    assert os.environ["MALLOC_ARENA_MAX"] == str(DEFAULT_ARENA_MAX)
