# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Section R: create, launch, interact, reload, and tear down real sessions."""

from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from tests.browser_helpers.browser_layout import browser  # noqa: F401
from tests.gate_harness import gate_http_port  # noqa: F401
from tests.native_gate_fixtures import gate_live_server  # noqa: F401
from tests.gate_harness import gate_runtime_paths  # noqa: F401
from tests.gate_harness import gate_tmux  # noqa: F401
from tests.tmux_runtime import run_isolated_tmux
from tests.tmux_runtime import wait_for_isolated_tmux_panes
from yomux_lib.local_services.registry import pid_is_serving
from yomux_lib.local_services.registry import process_group_has_serving_member
from yomux_lib.local_services.registry import process_state
from yomux_lib.tmux.session_retirement import capture_tmux_session_retirement
from yomux_lib.tmux.session_retirement import join_tmux_session_retirement
from yomux_lib.tmux.session_retirement import retained_tmux_session_births


NEW_SESSION = "1"

# Every browser test in this module is marked individually so the one non-browser negative
# control can declare itself.
pytestmark = pytest.mark.socket


def _pane_process_group(tmux_runtime) -> tuple[int, int]:
    result = run_isolated_tmux(tmux_runtime, "display-message", "-p", "-t", f"{NEW_SESSION}:", "#{pane_pid}")
    assert result.returncode == 0, result.stderr or result.stdout
    pane_pid = int(result.stdout.strip())
    return pane_pid, os.getpgid(pane_pid)


def _start_foreign_socket_session(tmux_runtime, socket_path: Path) -> None:
    """Start a same-named session on a second tmux socket the fixture must never touch."""

    started = subprocess.run(
        (tmux_runtime.tmux_binary, "-S", str(socket_path), "new-session", "-d", "-s", NEW_SESSION),
        capture_output=True,
        text=True,
        timeout=8,
        check=False,
    )
    assert started.returncode == 0, started.stderr or started.stdout


def _start_ready_retirement_session(tmux_runtime) -> None:
    """Launch a deterministic long-lived process and wait for its explicit READY."""

    source = "import signal; print('R6_RETIREMENT_READY', flush=True); signal.pause()"
    created = run_isolated_tmux(
        tmux_runtime,
        "new-session",
        "-d",
        "-s",
        NEW_SESSION,
        sys.executable,
        "-u",
        "-c",
        source,
    )
    assert created.returncode == 0, created.stderr or created.stdout
    ready, panes = wait_for_isolated_tmux_panes(
        tmux_runtime,
        (NEW_SESSION,),
        lambda captures: "R6_RETIREMENT_READY" in captures.get(NEW_SESSION, ""),
        timeout=5,
    )
    assert ready, panes


def _foreign_socket_has_session(tmux_runtime, socket_path: Path) -> int:
    return subprocess.run(
        (tmux_runtime.tmux_binary, "-S", str(socket_path), "has-session", "-t", f"{NEW_SESSION}:"),
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    ).returncode


def _process_group_exists(process_group_id: int) -> bool:
    """Report a group alive only while it holds a live, non-zombie member.

    A raw ``os.killpg(pgid, 0)`` counts a zombie that still retains the PGID as a
    live member, so under full-gate contention the transient zombie window after a
    kill outlives R6's tolerance and a dead group reads as alive. Route the oracle
    through the shared serving-member predicate, which excludes zombies exactly as
    production's ``bounded_process_table`` does.
    """

    return process_group_has_serving_member(process_group_id)


@pytest.mark.no_browser
def test_r6_kill_invariants_reject_a_surviving_group_and_a_killed_foreign_socket(gate_tmux):
    """Negative control for R6: each kill invariant must take BOTH values on real processes.

    R6 only protects anything if its three predicates can fail. A green R6 whose predicates are
    vacuous -- a process group that always reads dead, a foreign socket that always reads alive --
    would report the exact same result while a kill leaked across sockets. Drive each predicate
    through both states directly, without the browser, so the assertions R6 relies on are proven
    discriminating rather than merely satisfied.
    """

    _start_ready_retirement_session(gate_tmux)
    pane_pid, process_group_id = _pane_process_group(gate_tmux)
    assert pane_pid > 1 and process_group_id > 1
    retirement_identity = capture_tmux_session_retirement(NEW_SESSION)

    foreign_socket = gate_tmux.socket_dir / "negative-control-s"
    _start_foreign_socket_session(gate_tmux, foreign_socket)
    try:
        # Live state: every predicate reports the session and its process group as present.
        assert _process_group_exists(process_group_id) is True
        assert retained_tmux_session_births(retirement_identity)
        assert run_isolated_tmux(gate_tmux, "has-session", "-t", f"{NEW_SESSION}:").returncode == 0
        assert _foreign_socket_has_session(gate_tmux, foreign_socket) == 0

        # A kill scoped to the fixture socket must be visible in both fixture-socket predicates.
        killed = run_isolated_tmux(gate_tmux, "kill-session", "-t", f"{NEW_SESSION}:")
        assert killed.returncode == 0, killed.stderr or killed.stdout
        join_tmux_session_retirement(retirement_identity)
        assert retained_tmux_session_births(retirement_identity) == ()
        assert run_isolated_tmux(gate_tmux, "has-session", "-t", f"{NEW_SESSION}:").returncode != 0

        # The foreign-socket predicate is the one that catches a kill leaking to another server.
        # Prove it reports the violation by killing that server and reading it again.
        subprocess.run(
            (gate_tmux.tmux_binary, "-S", str(foreign_socket), "kill-server"),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        assert _foreign_socket_has_session(gate_tmux, foreign_socket) != 0
    finally:
        subprocess.run(
            (gate_tmux.tmux_binary, "-S", str(foreign_socket), "kill-server"),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )


@pytest.mark.no_browser
@pytest.mark.skipif(not hasattr(os, "fork"), reason="zombie lifecycle is POSIX-only")
def test_r6_process_group_oracle_reports_a_zombie_only_group_as_dead():
    """A group whose only member is an unreaped zombie must read as NOT surviving.

    This is the exact class that reddens R6 under load: after a kill the tmux pane
    briefly lingers as a zombie that keeps its PGID, so the retired ``killpg(pgid,
    0)`` oracle reads the dead group as alive. Forge that state directly -- a child
    made its own group leader that exits without being reaped -- and prove the old
    oracle mis-reads it while the shared serving-member predicate does not. Then add
    a genuinely live member to the same group and prove the predicate still reports
    it, so a real survivor cannot be ignored.
    """

    zombie_pid = os.fork()
    if zombie_pid == 0:  # pragma: no cover - child never returns
        os.setpgid(0, 0)
        os._exit(0)
    live_pid = 0
    try:
        os.setpgid(zombie_pid, zombie_pid)
        group_id = zombie_pid
        deadline = time.monotonic() + 2.0
        while process_state(zombie_pid) != "Z" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert process_state(zombie_pid) == "Z", process_state(zombie_pid)

        # Red for the retired oracle: the zombie retains the PGID, so a raw group
        # probe reports the dead group as alive.
        raw_killpg_alive = True
        try:
            os.killpg(group_id, 0)
        except ProcessLookupError:
            raw_killpg_alive = False
        assert raw_killpg_alive is True

        # Green for the shared predicate: a zombie-only group is not surviving.
        assert process_group_has_serving_member(group_id) is False
        assert _process_group_exists(group_id) is False

        # Fails closed: a genuinely live member of the same group must still read
        # as surviving, so the fix ignores zombies, never live survivors.
        live_pid = os.fork()
        if live_pid == 0:  # pragma: no cover - child never returns
            os.setpgid(0, group_id)
            signal.pause()
            os._exit(0)
        os.setpgid(live_pid, group_id)
        settle = time.monotonic() + 2.0
        while pid_is_serving(live_pid) is False and time.monotonic() < settle:
            time.sleep(0.01)
        assert pid_is_serving(live_pid) is True
        assert process_group_has_serving_member(group_id) is True
        assert _process_group_exists(group_id) is True
    finally:
        if live_pid:
            os.kill(live_pid, signal.SIGKILL)
            os.waitpid(live_pid, 0)
        os.waitpid(zombie_pid, 0)
