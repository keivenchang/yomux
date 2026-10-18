# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Real-kernel negative control for the developer inotify resource census."""

from __future__ import annotations

import os

import shutil

import subprocess

import sys

import tempfile

import time

from pathlib import Path

from tools.tmux_paths import short_temp_base, short_temp_prefix

import pytest

from tools.instance_isolation import PRODUCT_ROOT_KEYS

from tools.product_environment import product_env_name

from yomux_lib.infra.inotify_capacity import inotify_watch_descriptor_count

ROOT = Path(__file__).resolve().parents[1]

INDEXED_TREE_DIRECTORIES = 60

linux_only = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="inotify watch descriptors are a Linux kernel concept; Darwin FSEvents has no /proc/<pid>/fdinfo equivalent",
)

@pytest.fixture
def short_tmp_root():
    """A SHORT fixture-owned /tmp root, because AF_UNIX paths cap at 107 bytes.

    pytest's ``tmp_path`` nests worker, session and test-name segments and already exceeds the cap
    before the product appends ``runtime/control/<socket>``. The daemon then refuses to start, which
    from the parent is indistinguishable from a daemon that started and registered nothing.
    """
    base = Path(tempfile.mkdtemp(prefix=short_temp_prefix("ywd-"), dir=short_temp_base()))
    try:
        yield base
    finally:
        shutil.rmtree(base, ignore_errors=True)


def _build_indexed_tree(base: Path) -> tuple[Path, Path, int]:
    """A nested indexed root plus a separate visible root, and the tree's real directory count."""
    tree = base / "indexed"
    tree.mkdir()
    for index in range(INDEXED_TREE_DIRECTORIES):
        child = tree / f"d{index:03d}"
        child.mkdir()
        (child / "deep").mkdir()
    visible = base / "visible"
    visible.mkdir()
    directories = 1 + sum(1 for path in tree.rglob("*") if path.is_dir())
    return tree, visible, directories


def _isolated_child_env(base: Path) -> dict[str, str]:
    """Fixture-owned product root, TMPDIR and bytecode cache; nothing shared, nothing ambient.

    ``PYTHONPYCACHEPREFIX`` must be dropped rather than inherited: the launcher refuses a bytecode
    cache resolving outside ``YOLOMUX_ROOT``, and an inherited one silently kills the child. That
    failure is invisible from the parent, which then measures a dead pid and reads zero watches --
    a passing result for the wrong reason.
    """
    env = {key: value for key, value in os.environ.items() if key not in PRODUCT_ROOT_KEYS}
    root = base / "root"
    tmp = base / "tmp"
    root.mkdir()
    tmp.mkdir()
    env.update({product_env_name("ROOT"): str(root), "TMPDIR": str(tmp)})
    return env


def _await_watch_descriptors(child: subprocess.Popen, *, at_least: int, timeout: float = 15.0) -> int:
    """Poll until the kernel shows the registration, so the assertion is not a race on startup."""
    deadline = time.monotonic() + timeout
    observed = 0
    while time.monotonic() < deadline:
        assert child.poll() is None, "watchd child died before its watches could be measured"
        observed = inotify_watch_descriptor_count(child.pid)
        if observed >= at_least:
            return observed
        time.sleep(0.1)
    return observed


def _stop(child: subprocess.Popen) -> None:
    child.terminate()
    try:
        child.wait(timeout=10)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait(timeout=10)


@linux_only
@pytest.mark.socket
def test_the_kernel_watch_counter_does_rise_with_a_recursive_registration(short_tmp_root):
    """Negative control: an assertion that cannot fail proves nothing about the daemon.

    The test above passes if the counter is broken and always returns a small number. This drives
    the SAME tree through a deliberately recursive ``watchfiles`` registration in an isolated child
    and requires the count to scale with the directory count. Only with this row does a low count
    in the test above mean the topology is shallow rather than the measurement being blind.
    """
    tree, _visible, tree_directories = _build_indexed_tree(short_tmp_root)
    boot = (
        f"import sys; sys.path.insert(0, {str(ROOT)!r})\n"
        "import threading, time\n"
        "from watchfiles import watch as watchfiles_watch\n"
        "stop = threading.Event()\n"
        f"it = watchfiles_watch({str(tree)!r}, recursive=True, stop_event=stop, yield_on_timeout=True, rust_timeout=200)\n"
        "for _ in it:\n"
        "    pass\n"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", boot],
        env=_isolated_child_env(short_tmp_root),
        cwd=str(short_tmp_root),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        observed = _await_watch_descriptors(child, at_least=tree_directories)
        assert observed >= tree_directories, (
            f"a recursive registration over {tree_directories} directories produced only {observed} "
            f"kernel watches; the counter cannot detect an O(tree) topology and the shallow "
            f"assertion above is therefore vacuous"
        )
    finally:
        _stop(child)
