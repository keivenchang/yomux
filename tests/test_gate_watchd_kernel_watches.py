# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Kernel-layer proof that a large indexed root does not become a large watch topology.

Every existing cap and recursion test counts entries in the Python tuple watchd hands to
``watchfiles_watch``. That proves what the daemon INTENDED to register. It cannot prove what the
kernel actually holds: a recursive registration, a library that expands a root, or a retained
generation would all be invisible to a set count and would all show up here.

The distinction is the whole incident. The 2026-08-11 measurement recorded ONE recursive
registration holding 126,028 ``inotify wd:`` records at 155.5 MiB RSS. Instance count read as one.
Only the watch-descriptor count separates that from a healthy shallow topology.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from tools.instance_isolation import PRODUCT_ROOT_KEYS
from yolomux_lib.infra.inotify_capacity import inotify_watch_descriptor_count
from yolomux_lib.watchd_client import WatchClient

ROOT = Path(__file__).resolve().parents[1]
# Enough directories that an O(tree) topology is unmistakable next to an O(visible) one, while the
# fixture still builds in well under a second.
INDEXED_TREE_DIRECTORIES = 60
# The shallow class registers visible roots plus exact-file parents. This fixture declares one
# visible root and one settings/attention parent, so a correct daemon holds a small constant.
MAX_EXPECTED_SHALLOW_WATCHES = 8

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
    base = Path(tempfile.mkdtemp(prefix="ywd-", dir="/tmp"))
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
    env.update({"YOLOMUX_ROOT": str(root), "TMPDIR": str(tmp)})
    return env


def _spawn_watchd(base: Path, socket_path: Path) -> subprocess.Popen:
    boot = (
        f"import sys; sys.path.insert(0, {str(ROOT)!r})\n"
        "from pathlib import Path\n"
        "from yolomux_lib.watchd import PersistentWatchService\n"
        f"raise SystemExit(PersistentWatchService(Path({str(socket_path)!r}), idle_seconds=120.0).run())\n"
    )
    return subprocess.Popen(
        [sys.executable, "-c", boot],
        env=_isolated_child_env(base),
        cwd=str(base),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _await_socket(child: subprocess.Popen, socket_path: Path) -> None:
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if child.poll() is not None:
            pytest.fail(f"watchd child exited early: {child.stderr.read().decode()[-2000:]}")
        if socket_path.exists():
            return
        time.sleep(0.02)
    pytest.fail("fixture-owned watchd never created its listener socket")


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
def test_a_large_indexed_root_holds_no_kernel_watch_per_directory(short_tmp_root):
    """The box ':73' claim, measured where it is actually decided.

    A configured indexed root is covered by periodic reconciliation, never by a native
    registration, so the kernel watch count must track the VISIBLE roots and exact-file parents
    only. If a future change registers the indexed root -- or registers anything recursively --
    this count rises with the tree and this test fails, which a Python-set count could not do.
    """
    tree, visible, tree_directories = _build_indexed_tree(short_tmp_root)
    assert tree_directories >= 2 * INDEXED_TREE_DIRECTORIES, "fixture must be large enough to distinguish the two shapes"
    socket_path = short_tmp_root / "watchd.sock"
    child = _spawn_watchd(short_tmp_root, socket_path)
    try:
        _await_socket(child, socket_path)
        client = WatchClient(socket_path)
        lease = client.acquire_lease()
        assert lease.get("ok") is True, lease
        descriptor = {
            "descriptor_generation": 1,
            "expires_at": time.monotonic() + 300.0,
            "roots": [str(visible)],
            "files": [],
            "background_files": [],
            "transcripts": [],
            "repo_roots": [str(visible)],
            "indexed_dirs": [str(tree)],
            "skip_dirs": [],
            "settings_path": str(short_tmp_root / "settings.json"),
            "attention_path": str(short_tmp_root / "attention.json"),
            "configured_roots": [str(short_tmp_root)],
        }
        response = client.upsert(str(lease["lease_id"]), "kernel-watch-probe", descriptor)
        assert response.get("ok") is True, response

        observed = _await_watch_descriptors(child, at_least=1)

        assert observed >= 1, "no kernel watch was registered at all; the measurement proves nothing"
        assert observed <= MAX_EXPECTED_SHALLOW_WATCHES, (
            f"kernel holds {observed} inotify watch descriptors for {tree_directories} indexed "
            f"directories; a shallow topology must not scale with the tree"
        )
        assert observed < tree_directories // 4, (
            f"{observed} watches against {tree_directories} directories is not O(visible roots)"
        )
    finally:
        _stop(child)


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
        assert observed > MAX_EXPECTED_SHALLOW_WATCHES, "the two topologies must be distinguishable by this bound"
    finally:
        _stop(child)
