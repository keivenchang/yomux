# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Section M-DB: the stats database is partitioned per stable host.

`/home/keivenc` is NFS-exported and mounted by a second host at the same absolute
path, so an unpartitioned default puts two hosts on one SQLite inode. WAL does not
work across hosts, so the partition is what stops them meeting at all.
"""

from __future__ import annotations

from pathlib import Path

from tests.gate_harness import gate_runtime_paths  # noqa: F401
from yomux_lib.infra.host_identity import current_host_identity
from yomux_lib.stats_current import client as stats_client
from yomux_lib.stats_current import service as stats_service
from yomux_lib.stats_current import storage


def test_m_db_default_path_is_partitioned_by_stable_host_id(gate_runtime_paths):
    """Two hosts sharing one state directory must not resolve to one database file."""

    state_dir = gate_runtime_paths.state_dir / "m-db"
    resolved = stats_client.default_database_path(state_dir)
    identity = current_host_identity()

    assert identity.stable_host_id in resolved.parts, resolved
    assert resolved.name == storage.DATABASE_FILENAME, resolved
    assert resolved.parent != state_dir, "the database still sits directly in the shared state root"


def test_m_db_client_and_service_resolve_the_same_path(gate_runtime_paths):
    """One parent, not two copies.

    `client.default_database_path` and `service.default_database_path` were separate
    implementations of the same expression. Partitioning one and not the other would
    split the writer from its readers and is the exact defect shape this codebase
    keeps hitting.
    """

    state_dir = gate_runtime_paths.state_dir / "m-db-agree"
    assert stats_client.default_database_path(state_dir) == stats_service.default_database_path(state_dir)


def test_m_db_a_foreign_host_id_resolves_elsewhere(gate_runtime_paths, monkeypatch):
    """The partition must actually vary with the host, not merely contain a constant."""

    state_dir = gate_runtime_paths.state_dir / "m-db-foreign"
    ours = stats_client.default_database_path(state_dir)
    monkeypatch.setenv("YOLOMUX_HOST_ID", "some-other-machine")
    # current_host_identity is lru_cached so identity cannot drift mid-process.
    # Clearing it is the supported seam for asking "what would another host resolve?".
    current_host_identity.cache_clear()
    try:
        theirs = stats_client.default_database_path(state_dir)
    finally:
        current_host_identity.cache_clear()
    assert ours != theirs, (ours, theirs)
    assert "some-other-machine" in theirs.parts, theirs


def test_m_db_legacy_database_is_left_untouched(gate_runtime_paths):
    """Coexistence: a pre-existing unpartitioned database is never moved or deleted.

    THE SECOND RULE -- a new build must be able to run beside the old one without
    destroying what it wrote.
    """

    state_dir = gate_runtime_paths.state_dir / "m-db-legacy"
    state_dir.mkdir(parents=True, exist_ok=True)
    legacy = state_dir / storage.DATABASE_FILENAME
    legacy.write_bytes(b"pre-existing operator history")

    resolved = stats_client.default_database_path(state_dir)

    assert resolved != legacy
    assert legacy.read_bytes() == b"pre-existing operator history"
