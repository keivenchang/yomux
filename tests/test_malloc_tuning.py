# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Cap-the-glibc-arenas default so a hand-launched web process cannot balloon."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from yolomux_lib.infra.malloc_tuning import DEFAULT_ARENA_MAX
from yolomux_lib.infra.malloc_tuning import cap_malloc_arenas
from yolomux_lib.infra.worktree_writer import child_process_artifact_environment
from yolomux_lib.local_services.registry import inherited_python_path

ROOT = Path(__file__).resolve().parents[1]
ARENA_PROBE = ROOT / "tests" / "helpers" / "glibc_arena_probe.py"


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


def _fresh_statsd_arenas(environ: dict[str, str]) -> dict[str, object]:
    """Run the statsd-shaped arena probe in a child built exactly like `_spawn` builds one.

    `LocalServiceRegistry._spawn` hands its daemon `child_process_artifact_environment(...)`
    over `os.environ` plus `inherited_python_path`, so routing the probe through those two
    functions tests the real inheritance path rather than a hand-rolled copy of it.
    """

    env = child_process_artifact_environment(ROOT, environ=dict(environ))
    env["PYTHONPATH"] = inherited_python_path(env)
    completed = subprocess.run(
        [sys.executable, str(ARENA_PROBE)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_spawn_environment_hands_the_published_cap_to_children(monkeypatch):
    """The cap reaches statsd as inherited environment, which is the whole contract."""

    monkeypatch.delenv("MALLOC_ARENA_MAX", raising=False)
    cap_malloc_arenas()
    spawned = child_process_artifact_environment(ROOT, environ=dict(os.environ))
    assert spawned["MALLOC_ARENA_MAX"] == str(DEFAULT_ARENA_MAX)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="glibc malloc_info is Linux-only")
def test_capped_statsd_child_stays_at_two_arenas_under_construction_contention(monkeypatch):
    """Positive control: the inherited cap holds while eight threads build the service."""

    monkeypatch.delenv("MALLOC_ARENA_MAX", raising=False)
    cap_malloc_arenas()
    report = _fresh_statsd_arenas(dict(os.environ))
    assert report["malloc_arena_max"] == str(DEFAULT_ARENA_MAX)
    # Nothing the child imports may create an arena before the contention starts;
    # an import that quietly spawned a thread would shift both controls at once.
    assert report["arenas_before_threads"] == 1, report
    assert report["arenas"] <= DEFAULT_ARENA_MAX, report


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="glibc malloc_info is Linux-only")
def test_uncapped_statsd_child_opens_more_than_two_arenas(monkeypatch):
    """Negative control: without the inherited cap the same construction runs past two.

    Without this the positive control passes trivially on any day the contention fails
    to materialize, so this test is what makes the other one load-bearing.
    """

    monkeypatch.delenv("MALLOC_ARENA_MAX", raising=False)
    environ = {key: value for key, value in os.environ.items() if key != "MALLOC_ARENA_MAX"}
    report = _fresh_statsd_arenas(environ)
    assert report["malloc_arena_max"] is None
    assert report["arenas"] > DEFAULT_ARENA_MAX, report
