# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Go backend lifecycle acceptance for state isolation and restart accounting."""

from __future__ import annotations

import json
import hashlib
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from tests.isolated_dev_server import IsolatedDevServer
from tests.isolated_dev_server import isolated_dev_server_factory  # noqa: F401 -- fixture

pytestmark = pytest.mark.socket
SETTLE_SECONDS = 2.0


def _health_path(server: IsolatedDevServer) -> Path:
    return server.paths.state_dir / "go-backend-health" / f"{server.port}.json"


def _read_health(path: Path) -> dict[str, object]:
    document = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(document, dict), document
    return document


def _snapshot_tree(root: Path) -> dict[str, tuple[int, int, str]]:
    snapshot: dict[str, tuple[int, int, str]] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            stat = path.stat()
            snapshot[str(path.relative_to(root))] = (stat.st_size, stat.st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest())
    return snapshot


def test_a4_teardown_writes_nothing_more_and_a_second_port_touches_no_first_port_file(
    isolated_dev_server_factory: Callable[..., IsolatedDevServer],
) -> None:
    """A stopped Go process leaves its lifecycle record stable and port-scoped."""

    first = isolated_dev_server_factory("a4-first")
    first.assert_serving()
    first_health_path = _health_path(first)
    first_health = _read_health(first_health_path)
    assert first_health["process_start_count"] == 1, first_health
    first.stop()
    assert first.process.poll() is not None, first.output[-20:]
    stopped_snapshot = _snapshot_tree(first.paths.state_dir)
    assert first_health_path.relative_to(first.paths.state_dir).as_posix() in stopped_snapshot
    time.sleep(SETTLE_SECONDS)
    assert _snapshot_tree(first.paths.state_dir) == stopped_snapshot

    second = isolated_dev_server_factory("a4-second")
    second.assert_serving()
    assert second.port != first.port
    assert not second.paths.root.resolve(strict=False).is_relative_to(first.paths.root.resolve(strict=False))
    second_health = _read_health(_health_path(second))
    assert second_health["process_start_count"] == 1, second_health
    assert second_health["last_pid"] == second.process.pid, second_health
    assert _snapshot_tree(first.paths.state_dir) == stopped_snapshot
    second.stop()


def test_a5_clean_go_restart_continues_port_scoped_lifecycle_history(
    isolated_dev_server_factory: Callable[..., IsolatedDevServer],
    record_property,
) -> None:
    """A clean Go restart increments process/restart counts exactly once on the same port."""

    server = isolated_dev_server_factory("a5", env_overrides={"YOMUX_GO_START_REASON": "demand"})
    evidence: dict[str, object] = {"output": server.output}
    record_property("restart_lifecycle", evidence)
    try:
        server.assert_serving()
        original_port = server.port
        before = _read_health(_health_path(server))
        evidence["before"] = before
        assert before["process_start_count"] == 1, before
        assert before["restart_count"] == 0, before
        before_pid = server.process.pid

        def record_shutdown() -> None:
            stopped = _read_health(_health_path(server))
            evidence["stopped"] = stopped
            evidence["exit_status"] = server.process.returncode
            assert server.process.returncode == 0, evidence
            assert stopped["last_clean_shutdown"] is True, evidence
            shutdown = stopped["events"][-1]
            assert shutdown["kind"] == "shutdown" and shutdown["pid"] == before_pid, evidence
            assert shutdown["termination_signal"] in {"interrupt", "terminated"}, evidence
            assert shutdown["exit_status"] == 0 and shutdown["clean"] is True, evidence

        server.restart(after_stop=record_shutdown)
        server.assert_serving()
        assert server.port == original_port
        after = _read_health(_health_path(server))
        evidence["after"] = after
        assert after["process_start_count"] == 2, after
        assert after["restart_count"] == 1, after
        assert after["demand_start_count"] == 1, after
        assert after["unexpected_restart_count"] == 0, after
        assert after["last_pid"] == server.process.pid != before_pid, after
        assert after["last_clean_shutdown"] is False, after
    finally:
        server.stop()
