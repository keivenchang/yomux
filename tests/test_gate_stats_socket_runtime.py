# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""The stats socket lives on a host-local runtime root, and rollout stays reachable.

A Unix socket cannot live on a network filesystem, and `STATE_DIR` may be an
NFS-exported home. The socket therefore moves to `RUNTIME_DIR`, which is
host-partitioned and boot-scoped.

The legacy probe is the coexistence half: a server from the previous build is
running right now with its socket at the old path, and a client that looked only
at the new path would declare a live service missing.
"""

from __future__ import annotations

import threading
from pathlib import Path

from tests.gate_harness import gate_runtime_paths  # noqa: F401
from yolomux_lib.infra import common
from yolomux_lib.stats_current import client as stats_client
from yolomux_lib.stats_current import service as stats_service
from yolomux_lib.stats_current import storage
from yolomux_lib.stats_current.transcripts import StatsCurrentTranscriptUsageScanner
from yolomux_lib.stats_current.usage import usage_atom_from_source
from tools.mockers.transcript import codex_meta
from tools.mockers.transcript import codex_usage
from tools.mockers.transcript import write_records


def test_client_and_service_resolve_one_socket_path(gate_runtime_paths):
    """One parent. These were two separate copies of the same expression."""

    assert stats_client.default_socket_path() == stats_service.default_socket_path()


def test_default_socket_is_on_the_host_local_runtime_root(gate_runtime_paths):
    resolved = stats_client.default_socket_path()
    assert resolved.name.startswith(f"{storage.SOCKET_FILENAME.removesuffix('.sock')}.")
    assert resolved.name.endswith(".sock")
    assert common.RUNTIME_DIR in resolved.parents, resolved


def test_distinct_state_roots_never_attach_to_the_same_statsd(gate_runtime_paths, tmp_path, monkeypatch):
    """The socket must identify the database owner, not only its schema."""

    first_state = tmp_path / "first-state"
    second_state = tmp_path / "second-state"

    first_socket = storage.default_socket_path(first_state)
    second_socket = storage.default_socket_path(second_state)

    assert first_socket != second_socket
    assert storage.default_socket_path(first_state) == first_socket
    assert stats_client.default_socket_path(first_state) == stats_service.default_socket_path(first_state)
    assert first_socket.parent == second_socket.parent == common.RUNTIME_DIR / "services"

    monkeypatch.setattr(common, "STATE_DIR", first_state)
    first_client = stats_client.StatsCurrentClient()
    monkeypatch.setattr(common, "STATE_DIR", second_state)
    second_client = stats_client.StatsCurrentClient()

    assert first_client.database_path != second_client.database_path
    assert first_client._transport.socket_path != second_client._transport.socket_path


def test_two_state_roots_append_usage_atoms_to_their_own_statsd(gate_runtime_paths, tmp_path):
    """A scanned transcript reaches only the statsd that owns its state root."""

    transcript = tmp_path / "usage.jsonl"
    write_records(transcript, [
        codex_meta("two-root-thread", model="gpt-test"),
        codex_usage(1, 10, 5, 1),
    ])
    scan = StatsCurrentTranscriptUsageScanner().scan([
        {"key": "two-root|0|codex", "kind": "codex", "transcript": str(transcript)},
    ])
    atoms = tuple(usage_atom_from_source({
        **vars(item.atom), "tmux_key": item.tmux_key, "agent_kind": item.agent_kind,
    }) for item in scan.items)
    assert atoms

    services = []
    clients = []
    try:
        for name in ("first", "second"):
            state_dir = tmp_path / name
            database_path = storage.default_database_path(state_dir)
            socket_path = storage.default_socket_path(state_dir)
            service = stats_service.StatsCurrentService(
                socket_path,
                database_path,
                idle_seconds=60,
                clock=lambda: 100_000.0,
            )
            thread = threading.Thread(target=service.run, daemon=True)
            thread.start()
            assert service.cache_ready_event.wait(20), service._status()
            services.append((service, thread, database_path))
            clients.append(stats_client.StatsCurrentClient(socket_path, database_path))

        for index, client in enumerate(clients, start=1):
            response = client.append(usage_atoms=atoms)
            assert response["counts"]["usage_atoms_accepted"] == len(atoms), response

        expected_ids = [atom.event_id for atom in atoms]
        for _service, _thread, database_path in services:
            with storage.Store.open_reader(database_path) as store:
                assert [atom.event_id for atom in store.read_snapshot().usage_atoms] == expected_ids
    finally:
        for service, _thread, _database_path in services:
            service.stop()
        for _service, thread, _database_path in services:
            thread.join(timeout=3)
            assert thread.is_alive() is False


def test_a_live_legacy_socket_is_still_reached_during_rollout(gate_runtime_paths, tmp_path):
    """A previous build is serving on the old path; do not declare it missing."""

    state_dir = tmp_path / "legacy-state"
    legacy = state_dir / "services" / storage.SOCKET_FILENAME
    legacy.parent.mkdir(parents=True)
    legacy.touch()

    assert storage.default_socket_path(state_dir) == legacy


def test_absent_legacy_socket_does_not_pin_the_old_path(gate_runtime_paths, tmp_path):
    """Once nothing is serving there, new runs use the runtime root."""

    state_dir = tmp_path / "empty-state"
    state_dir.mkdir()
    resolved = storage.default_socket_path(state_dir)
    assert resolved != state_dir / "services" / storage.SOCKET_FILENAME
    assert common.RUNTIME_DIR in resolved.parents, resolved
