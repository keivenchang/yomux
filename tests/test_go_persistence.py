# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Cold Go startup and restart use databases created by the Go writer."""

import sqlite3

import pytest
from selenium.webdriver.support.ui import WebDriverWait

from tests.isolated_dev_server import isolated_dev_server_factory  # noqa: F401


pytestmark = [pytest.mark.go_backend, pytest.mark.socket, pytest.mark.no_browser]


def _schema(server):
    path = server.paths.state_dir / "stats-v9.sqlite3"
    assert path.is_file(), (path, server.output)
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as database:
        tables = {row[0] for row in database.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        metadata = database.execute("SELECT minimum_writer_protocol, minimum_writer_build FROM schema_meta WHERE singleton=1").fetchone()
    assert {"schema_meta", "observations", "usage_atoms", "aggregate_publication", "aggregate_ring_slots"} <= tables
    assert metadata is not None
    return tables, metadata


def _observations(server):
    path = server.paths.state_dir / "stats-v9.sqlite3"
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as database:
        return set(database.execute("SELECT * FROM observations"))


def _collect_go_facts(server):
    status, _headers, body = server.request("/api/stats-snapshot?range_seconds=300&resolution=10&client_id=persistence-test")
    assert status in {200, 202}, (status, body)
    # Collector startup extends the native schema with checkpoint ownership.
    # Snapshot after that transition and the first fact commit, so restart
    # comparisons do not race the initial migration.
    return WebDriverWait(server, 10).until(lambda owner: _observations(owner) if "collector_checkpoints" in _schema(owner)[0] else False)


def _collector_environment(root):
    claude, codex = root / "claude", root / "codex"
    claude.mkdir(parents=True)
    codex.mkdir()
    return {
        "YOMUX_STATS_DISABLE_COLLECTORS": "0", "YOLOMUX_STATS_DISABLE_COLLECTORS": "0",
        "YOMUX_STATS_SYNTHETIC_HOST": "1", "YOMUX_STATS_SYNTHETIC_INVENTORY": "1",
        "YOMUX_CLAUDE_TRANSCRIPT_ROOT": str(claude), "YOMUX_CODEX_TRANSCRIPT_ROOT": str(codex),
        "YOMUX_OPENCODE_DATABASE": str(root / "absent-opencode.db"),
    }


def test_go_cold_start_and_restart_preserve_native_schema_and_unrelated_files(tmp_path, isolated_dev_server_factory):
    state = tmp_path / "retained-state"
    state.mkdir()
    unrelated = {"plugin.data": b"unrelated plugin bytes\x00\xff", "stats-v6.sqlite3": b"retired file preserved as unrelated data"}
    for name, content in unrelated.items():
        (state / name).write_bytes(content)
    assert not (state / "stats-v9.sqlite3").exists()
    server = isolated_dev_server_factory("cold", state_dir=state, env_overrides=_collector_environment(tmp_path / "sources"))
    facts = _collect_go_facts(server)
    before = _schema(server)
    identity = (state / "stats-v9.sqlite3").stat().st_ino
    server.restart()
    assert _schema(server) == before
    assert facts <= _observations(server)
    assert (state / "stats-v9.sqlite3").stat().st_ino == identity
    assert {name: (state / name).read_bytes() for name in unrelated} == unrelated
    assert not list(state.glob("*.corrupt*"))


def test_concurrent_go_instances_keep_native_databases_and_restart_isolated(tmp_path, isolated_dev_server_factory):
    first = isolated_dev_server_factory("first-native", env_overrides=_collector_environment(tmp_path / "first-sources"))
    second = isolated_dev_server_factory("second-native", env_overrides=_collector_environment(tmp_path / "second-sources"))
    first_facts, second_facts = _collect_go_facts(first), _collect_go_facts(second)
    first_schema, second_schema = _schema(first), _schema(second)
    first_path = first.paths.state_dir / "stats-v9.sqlite3"
    second_path = second.paths.state_dir / "stats-v9.sqlite3"
    assert first_path != second_path and first_path.stat().st_ino != second_path.stat().st_ino
    assert first.port != second.port and first.tmux.socket_path != second.tmux.socket_path
    peer_pid = second.process.pid
    first.restart()
    assert _schema(first) == first_schema and _schema(second) == second_schema
    assert first_facts <= _observations(first) and second_facts <= _observations(second)
    assert second.process.pid == peer_pid
    first.assert_serving()
    second.assert_serving()
