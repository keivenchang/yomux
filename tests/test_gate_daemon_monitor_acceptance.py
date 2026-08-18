# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""F3 acceptance items A4 and A5, driven against a real isolated dev server.

These are the daemon-monitor acceptance items that were blocked only on M13 (an isolated dev
server actually running). They are deliberately black-box: every claim is made against the running
`yolomux.py` process and the files it writes under its own fixture root, never against an in-process
app object.

  * A4 -- teardown isolation, proven by BYTES and MTIME, not by inspection. (a) A stopped server
    writes nothing more into its own state directory. (b) A second server on a DIFFERENT port, with
    its own `/tmp` root, does not touch the first server's state file.
  * A5 -- restart-sequence acceptance. After a real service restart and then a real web restart on
    the SAME port, the retained backend-health history at `<root>/state/backend-health/<port>.json`
    continues the same observer epoch (or names an explicit reset reason), advances its revision
    without rewinding, is written by a new pid, and does not double-count the service restart.

A1 (statsd self-recovery) lives in `test_gate_daemon_monitor_statsd_recovery.py`.
"""

from __future__ import annotations

import json
import os
import signal
import time
from collections.abc import Callable
from http import HTTPStatus
from pathlib import Path

import pytest

from tests.isolated_dev_server import IsolatedDevServer
from tests.isolated_dev_server import isolated_dev_server_factory  # noqa: F401  -- fixture
from tests.isolated_dev_server import pid_is_alive
from tests.isolated_dev_server import process_descendants
from tests.isolated_dev_server import reap_descendants
from tests.isolated_dev_server import stop_and_reap_daemons
from yolomux_lib.host_identity import process_start_identity


pytestmark = pytest.mark.socket

# The observer schedules on BACKEND_HEALTH_OBSERVE_SECONDS = 2.0, so one settle window has to be
# comfortably longer than a single cadence to catch a writer thread that survived teardown or a
# revision that has not advanced yet.
HEALTH_SETTLE_SECONDS = 8.0
HEALTH_POLL_SECONDS = 0.2


def _health_path(server: IsolatedDevServer) -> Path:
    return server.paths.state_dir / "backend-health" / f"{server.port}.json"


def _read_health_document(path: Path) -> dict[str, object] | None:
    """Read the retained health document, tolerating an in-flight atomic replace."""

    try:
        raw = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return None
    try:
        document = json.loads(raw)
    except ValueError:
        return None
    return document if isinstance(document, dict) else None


def _wait_for_health_document(
    server: IsolatedDevServer,
    *,
    min_revision: int = 0,
    timeout_seconds: float = HEALTH_SETTLE_SECONDS,
) -> dict[str, object]:
    """Block until the retained health document exists and its revision is at least `min_revision`."""

    path = _health_path(server)
    deadline = time.monotonic() + timeout_seconds
    last: dict[str, object] | None = None
    while time.monotonic() < deadline:
        document = _read_health_document(path)
        if document is not None:
            last = document
            if int(document.get("revision") or 0) >= min_revision:
                return document
        time.sleep(HEALTH_POLL_SECONDS)
    raise AssertionError(
        f"health document at {path} never reached revision >= {min_revision}: last={last}, "
        f"output={server.output[-20:]}"
    )


def _trigger_stats(server: IsolatedDevServer) -> None:
    """Demand the stats snapshot so statsd is spawned and becomes a monitored resource."""

    status, _headers, body = server.request(
        "/api/stats-snapshot?range_seconds=300&resolution=AUTO&client_id=a5-acceptance"
    )
    assert status != HTTPStatus.INTERNAL_SERVER_ERROR, (status, body, server.output[-20:])


def _restart_counts(document: dict[str, object]) -> dict[str, int]:
    resources = document.get("resources")
    if not isinstance(resources, dict):
        return {}
    counts: dict[str, int] = {}
    for name, value in resources.items():
        aggregate = value.get("aggregate") if isinstance(value, dict) else None
        if isinstance(aggregate, dict):
            counts[str(name)] = int(aggregate.get("restart_count") or 0)
    return counts


def _snapshot_tree(root: Path) -> dict[str, tuple[int, int]]:
    """Every file under `root`, keyed by relative path, valued by (size, mtime_ns)."""

    snapshot: dict[str, tuple[int, int]] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        snapshot[str(path.relative_to(root))] = (stat.st_size, stat.st_mtime_ns)
    return snapshot


def _tree_changes(
    before: dict[str, tuple[int, int]],
    after: dict[str, tuple[int, int]],
) -> dict[str, tuple[tuple[int, int] | None, tuple[int, int] | None]]:
    return {
        key: (before.get(key), after.get(key))
        for key in sorted(before.keys() | after.keys())
        if before.get(key) != after.get(key)
    }


def _kill_service_descendant(descendants: list[tuple[int, str, str]], module_marker: str) -> int | None:
    """Kill one captured service daemon selected by its command line, through its exact identity.

    Returns the pid signalled, or None when no matching live descendant exists. This forces a real
    SERVICE restart: the running web observer sees the daemon go absent and a fresh process epoch
    when it is re-demanded.
    """

    for pid, identity, cmdline in descendants:
        if module_marker not in cmdline:
            continue
        if not pid_is_alive(pid) or process_start_identity(pid) != identity:
            continue
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            return None
        return pid
    return None


def _wait_state_dir_quiescent(state_dir: Path, *, timeout_seconds: float = HEALTH_SETTLE_SECONDS) -> dict[str, tuple[int, int]]:
    """Poll until the state dir stops changing across two consecutive reads, then return that snapshot."""

    deadline = time.monotonic() + timeout_seconds
    previous = _snapshot_tree(state_dir)
    while time.monotonic() < deadline:
        time.sleep(HEALTH_POLL_SECONDS * 3)
        current = _snapshot_tree(state_dir)
        if current == previous:
            return current
        previous = current
    return previous


@pytest.mark.skipif(not Path("/proc").is_dir(), reason="reads the running process environment from /proc")
def test_a4_teardown_writes_nothing_more_and_a_second_port_touches_no_first_port_file(
    isolated_dev_server_factory: Callable[..., IsolatedDevServer],
) -> None:
    """A4: teardown isolation as byte/mtime checks, across two genuinely isolated servers."""

    first = isolated_dev_server_factory("a4-first")
    first.assert_serving()
    _trigger_stats(first)
    # Let the health writer run at least one cycle so the state directory has live content whose
    # stillness after teardown is a real claim, not a claim about an empty directory.
    _wait_for_health_document(first, min_revision=1)

    # Tear the instance down completely: the web process AND the per-instance service daemons it
    # spawned. statsd is a shared daemon that outlives the web process by design, so stopping only
    # the web process leaves it writing its database -- the state directory is not quiescent until
    # the daemons are reaped too.
    reaped = stop_and_reap_daemons(first)
    assert first.process.poll() is not None, first.output[-20:]

    # (a) After teardown, the stopped instance's own state directory sees NO further file activity.
    # Poll to a quiescent snapshot (teardown complete), then wait longer than one observer cadence
    # and snapshot again: a writer thread or daemon that survived teardown would move a size or an
    # mtime here.
    after_stop = _wait_state_dir_quiescent(first.paths.state_dir)
    assert after_stop, first.paths.state_dir
    time.sleep(HEALTH_SETTLE_SECONDS)
    settled = _snapshot_tree(first.paths.state_dir)
    post_teardown_changes = _tree_changes(after_stop, settled)
    assert not post_teardown_changes, (
        f"a4(a): {first.label} state dir changed after teardown (reaped daemons {reaped}): "
        f"{post_teardown_changes}"
    )

    # (b) A second server on a DIFFERENT port, with its own /tmp root, must not touch the first
    # server's state file. Freeze the first server's tree, run the second hard enough to write its
    # own health history, and prove the first server's bytes and mtimes are untouched.
    frozen_first = _snapshot_tree(first.paths.state_dir)
    second = isolated_dev_server_factory("a4-second")
    second.assert_serving()
    assert second.port != first.port
    assert not second.paths.root.resolve(strict=False).is_relative_to(first.paths.root.resolve(strict=False))
    _trigger_stats(second)
    second_health = _wait_for_health_document(second, min_revision=1)
    # The second server really did write into its OWN state dir -- otherwise "it did not touch the
    # first" would be vacuous.
    assert _health_path(second).exists()
    assert int(second_health.get("port") or 0) == second.port

    first_after_second = _snapshot_tree(first.paths.state_dir)
    cross_port_changes = _tree_changes(frozen_first, first_after_second)
    assert not cross_port_changes, (
        f"a4(b): second server on port {second.port} disturbed first server's stopped state dir: "
        f"{cross_port_changes}"
    )

    # Tear the second instance down completely too, so no daemon it spawned outlives this test.
    stop_and_reap_daemons(second)


def _statsd_restart_count(server: IsolatedDevServer) -> int:
    document = _read_health_document(_health_path(server))
    if document is None:
        return 0
    return _restart_counts(document).get("statsd", 0)


def _assert_restart_counts_bounded(
    before_counts: dict[str, int],
    after_counts: dict[str, int],
) -> None:
    """Every retained resource stays present and records at most one real parent-bound restart.

    Present FIRST, then exact-compare with NO default: a resource that VANISHED from the post-restart
    health document is a failure, not an "unchanged" pass. Reading the after value with a
    `before_count` fallback would compare a number against itself and never fail the disappearance
    case -- exactly the false green this guards.
    """

    vanished = sorted(set(before_counts) - set(after_counts))
    assert not vanished, (
        f"a5: monitored resource(s) disappeared from the health document after the web restart: "
        f"{vanished} (before={before_counts}, after={after_counts})"
    )
    for name, before_count in before_counts.items():
        assert after_counts[name] in {before_count, before_count + 1}, (
            f"a5: web restart changed restart_count for {name}: {before_count} -> {after_counts[name]} "
            "(a parent-bound daemon may restart once, but must not be double-counted)"
        )


def _epoch_pid(process_epoch: str) -> int:
    """The pid embedded in a `pid:<n>:start:<...>` / `pid:<n>:startid:<...>` process epoch."""

    parts = str(process_epoch or "").split(":")
    return int(parts[1]) if len(parts) >= 2 and parts[0] == "pid" and parts[1].isdigit() else 0


def _statsd_current(document: dict[str, object]) -> dict[str, object]:
    resources = document.get("resources")
    if isinstance(resources, dict):
        statsd = resources.get("statsd")
        if isinstance(statsd, dict):
            current = statsd.get("current")
            if isinstance(current, dict):
                return current
    return {}


def _wait_for_statsd_ready(
    server: IsolatedDevServer,
    *,
    exclude_epoch: str = "",
    timeout_seconds: float = 20.0,
) -> str:
    """Poll until statsd is observed READY with a verified process epoch, and return that epoch.

    `exclude_epoch` lets the caller wait for a DIFFERENT (i.e. restarted) statsd process: the first
    ready observation of an epoch is a baseline, so a restart is only counted once a SECOND verified
    epoch replaces the first. Re-demands stats each poll so a demand-started daemon actually spawns.
    """

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        _trigger_stats(server)
        current = _statsd_current(_read_health_document(_health_path(server)) or {})
        epoch = str(current.get("process_epoch") or "")
        if str(current.get("state")) == "ready" and epoch and epoch != "none" and epoch != exclude_epoch:
            return epoch
        time.sleep(0.5)
    raise AssertionError(
        f"statsd never reached ready with a verified epoch != {exclude_epoch!r}; "
        f"health={_read_health_document(_health_path(server))}"
    )


@pytest.mark.skipif(not Path("/proc").is_dir(), reason="reads the running process environment from /proc")
def test_a5_service_then_web_restart_continues_history_without_double_counting(
    isolated_dev_server_factory: Callable[..., IsolatedDevServer],
) -> None:
    """A5: a service restart, then a web restart on the same port, continues the retained history.

    Service daemons are parent-bound: a web restart retires the old generation even when its idle
    deadline has not elapsed. Retained history must distinguish that one real restart from a duplicate
    observation while preserving the observer epoch.
    """

    # Keep idle retirement out of the scenario so every daemon exit is attributable to parent death.
    server = isolated_dev_server_factory("a5", env_overrides={"YOLOMUX_LOCAL_SERVICE_IDLE_SECONDS": "45"})
    # Every daemon identity this test ever sees, so teardown can retire the UNION. The shared daemons
    # started before the web restart reparent to init when `restart()` stops the old web process, so
    # the replacement server's descendant tree does NOT include them -- reaping only current
    # descendants would leak them for the whole 45 s idle. Identity-fenced, never name-matched.
    captured_daemons: list[tuple[int, str, str]] = []
    try:
        server.assert_serving()
        original_port = server.port

        # Bring statsd up and wait until the observer has VERIFIED its first epoch (a baseline). Only
        # then can a restart be counted -- the first ready observation of any epoch is a baseline, so
        # killing statsd before it was ever verified would just make the replacement the baseline.
        _trigger_stats(server)
        _wait_for_health_document(server, min_revision=1)
        baseline_epoch = _wait_for_statsd_ready(server)
        baseline_statsd_pid = _epoch_pid(baseline_epoch)
        assert baseline_statsd_pid > 1, baseline_epoch

        # Force a real SERVICE restart: capture the live daemon tree while the server is alive, kill
        # statsd by its exact identity, and re-demand it. The running web observer sees the NEW epoch
        # replace the verified baseline and counts exactly one restart.
        descendants = process_descendants(server.process.pid)
        captured_daemons.extend(descendants)
        killed_pid = _kill_service_descendant(descendants, "stats_current.service")
        # No live statsd owner is a FAILURE, not a skip: without a real service restart A5 would go
        # green having never exercised the event it exists to test.
        assert killed_pid is not None, (
            f"a5: found no live statsd daemon to restart; captured descendants="
            f"{[(pid, cmd) for pid, _identity, cmd in descendants]}"
        )
        assert killed_pid == baseline_statsd_pid, (killed_pid, baseline_statsd_pid)

        restarted_epoch = _wait_for_statsd_ready(server, exclude_epoch=baseline_epoch)
        restarted_statsd_pid = _epoch_pid(restarted_epoch)
        # The central event: a service PID genuinely changed across the restart.
        assert restarted_statsd_pid > 1 and restarted_statsd_pid != baseline_statsd_pid, (
            f"a5: statsd pid did not change across the forced restart: {baseline_statsd_pid} -> "
            f"{restarted_statsd_pid} (epochs {baseline_epoch} -> {restarted_epoch})"
        )
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline and _statsd_restart_count(server) < 1:
            time.sleep(0.5)
        assert _statsd_restart_count(server) >= 1, (
            f"a5: killed statsd pid {killed_pid}, it restarted to pid {restarted_statsd_pid} but the "
            f"observer never counted the restart; health={_read_health_document(_health_path(server))}"
        )

        # Snapshot the retained history AFTER the service restart and BEFORE the web restart. This is
        # the baseline the web restart must continue, not reset and not double-count.
        before = _wait_for_health_document(server, min_revision=2)
        before_revision = int(before.get("revision") or 0)
        before_epoch = str(before.get("observer_epoch") or "")
        before_writer = before.get("writer")
        assert isinstance(before_writer, dict), before
        before_pid = int(before_writer.get("pid") or 0)
        before_counts = _restart_counts(before)
        assert before_epoch, before
        assert before_pid > 1, before
        assert before_counts.get("statsd", 0) >= 1, before_counts

        # Freeze the exact pre-restart identities. Parent-bound service generations must all retire;
        # a replacement is a real single restart, not an observation of the old orphan.
        pre_web_restart_daemons = process_descendants(server.process.pid)
        captured_daemons.extend(pre_web_restart_daemons)

        # The WEB restart: a new OS process binds the exact same port and re-reads retained history.
        server.restart()
        server.assert_serving()
        assert server.port == original_port
        deadline = time.monotonic() + 10.0
        surviving_old_daemons = []
        while time.monotonic() < deadline:
            surviving_old_daemons = [
                (pid, cmdline)
                for pid, identity, cmdline in pre_web_restart_daemons
                if pid_is_alive(pid) and process_start_identity(pid) == identity
            ]
            if not surviving_old_daemons:
                break
            time.sleep(HEALTH_POLL_SECONDS)
        assert not surviving_old_daemons, f"a5: parent-bound daemon(s) survived web restart: {surviving_old_daemons}"

        # After the web restart the history must ADVANCE (a higher revision written by a NEW pid)
        # while keeping the SAME observer epoch -- or, if it could not continue, name an explicit
        # reset reason.
        after = _wait_for_health_document(server, min_revision=before_revision + 1)
        after_revision = int(after.get("revision") or 0)
        after_epoch = str(after.get("observer_epoch") or "")
        after_writer = after.get("writer")
        assert isinstance(after_writer, dict), after
        after_pid = int(after_writer.get("pid") or 0)
        reset_reason = str(after.get("history_reset_reason") or "")
        coverage = str(after.get("history_coverage") or "")

        # Revision continuity: strictly forward, never rewound to a lower number by the new writer.
        assert after_revision > before_revision, (before_revision, after_revision, after)
        # A new process is writing.
        assert after_pid != before_pid, (before_pid, after_pid, after)
        assert after_pid == server.process.pid, (after_pid, server.process.pid, after)
        # Same history epoch OR an explicit, valid reset reason -- never a silent epoch change.
        if after_epoch != before_epoch:
            assert reset_reason, (
                f"a5: observer epoch changed {before_epoch} -> {after_epoch} with no history_reset_reason"
            )
            assert coverage == "reset", (coverage, reset_reason, after)
        else:
            assert coverage == "full", (coverage, after)
            assert not reset_reason, (reset_reason, after)

        # No double-counted restart: each demanded parent-bound daemon may add one real process epoch,
        # while an undemanded daemon retains its previous count. Every before-resource remains present.
        after_counts = _restart_counts(after)
        assert "statsd" in after_counts, (before_counts, after_counts)
        _assert_restart_counts_bounded(before_counts, after_counts)

        # Negative control: if statsd DISAPPEARED from the post-restart document, the preservation
        # check must FAIL rather than fall back to the old count and report "unchanged". Force it out
        # and prove the assertion fires -- otherwise the count check could not detect a vanished
        # resource at all.
        without_statsd = {name: count for name, count in after_counts.items() if name != "statsd"}
        with pytest.raises(AssertionError, match="disappeared"):
            _assert_restart_counts_bounded(before_counts, without_statsd)
    finally:
        # Reap the union of old identities and the replacement server's current descendants.
        if server.process.poll() is None:
            captured_daemons.extend(process_descendants(server.process.pid))
        server.stop()
        reap_descendants(captured_daemons)
