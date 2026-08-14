# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Red contracts for host-qualified tmux state and alert attribution."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from yolomux_lib import app as app_module
from yolomux_lib.infra import common
from yolomux_lib.infra.common import SessionInfo
from yolomux_lib.infra.common import TmuxPaneInfo
from yolomux_lib.infra.host_partition import host_namespaced_path
from yolomux_lib.observability.activity import ActivityLedger
from yolomux_lib.workspace import metadata
from tests.helpers.local_service_records import FixtureHostIdentityBuilder


TARGET = "yo7771:0.0"
SESSION = "yo7771"
WINDOW = "0"
PANE = "0"


def _host_identity(stable_host_id: str, display_hostname: str) -> Any:
    return FixtureHostIdentityBuilder(
        stable_host_id=stable_host_id,
        display_hostname=display_hostname,
        boot_id=f"boot-{stable_host_id}",
        pid=4242,
        process_start_ticks=5252,
        instance_nonce=f"instance-{stable_host_id}",
        stable_host_id_source="gate fixture",
    ).build()


def _pane(tmp_path: Path) -> TmuxPaneInfo:
    return TmuxPaneInfo(
        session=SESSION,
        window=WINDOW,
        pane=PANE,
        pane_id="%69",
        target=TARGET,
        current_path=str(tmp_path),
        command="claude",
        active=True,
        window_active=True,
        title="fixture agent",
        pid=4242,
        window_name="agent",
    )


def _session_info(tmp_path: Path) -> SessionInfo:
    pane = _pane(tmp_path)
    return SessionInfo(session=SESSION, panes=[pane], selected_pane=pane, agents=[])


def _status_app(identity: Any, state_root: Path) -> Any:
    webapp = object.__new__(app_module.TmuxWebtermApp)
    webapp.host_identity = identity
    webapp.state_dir = state_root
    webapp.tmux_ai_status_path = identity.namespaced_path(state_root, "tmux-AI-status.json")
    webapp.legacy_attention_acks_path = identity.namespaced_path(state_root, "attention-acks.json")
    return webapp


def _json_lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_host_namespaced_path_uses_stable_host_id_and_preserves_legacy_path(tmp_path: Path) -> None:
    identity = _host_identity("fixture-host-a", "lin1")
    legacy_path = tmp_path / "activity.json"

    assert host_namespaced_path(legacy_path, identity) == identity.namespaced_path(tmp_path, "activity.json")
    assert host_namespaced_path(legacy_path) == legacy_path


def test_identical_tmux_targets_on_two_hosts_remain_distinct_graph_records(tmp_path: Path) -> None:
    host_a = _host_identity("fixture-host-a", "lin1")
    host_b = _host_identity("fixture-host-b", "lin2")
    info = _session_info(tmp_path)

    graph_a = metadata.session_work_graph(
        info,
        metadata.MetadataCache(),
        allow_network=False,
        host_identity=host_a,
    )
    graph_b = metadata.session_work_graph(
        info,
        metadata.MetadataCache(),
        allow_network=False,
        host_identity=host_b,
    )

    for family in ("tmux_sessions", "tmux_windows", "tmux_panes"):
        keys_a = set(graph_a[family])
        keys_b = set(graph_b[family])
        assert len(keys_a) == 1
        assert len(keys_b) == 1
        assert keys_a.isdisjoint(keys_b), f"{family} overwrote the identical target on another host"
        assert all(host_a.stable_host_id in key for key in keys_a)
        assert all(host_b.stable_host_id in key for key in keys_b)

    pane_a = next(iter(graph_a["tmux_panes"].values()))
    pane_b = next(iter(graph_b["tmux_panes"].values()))
    assert pane_a["target"] == pane_b["target"] == TARGET


def test_tmux_payload_uses_stable_host_id_for_keys_and_hostname_for_display(tmp_path: Path) -> None:
    identities = (
        _host_identity("fixture-host-a", "lin1"),
        _host_identity("fixture-host-b", "lin2"),
    )
    graphs = [
        metadata.session_work_graph(
            _session_info(tmp_path),
            metadata.MetadataCache(),
            allow_network=False,
            host_identity=identity,
        )
        for identity in identities
    ]

    visible_rows = [next(iter(graph["tmux_panes"].values())) for graph in graphs]
    assert [row["hostname"] for row in visible_rows] == ["lin1", "lin2"]
    assert [row["stable_host_id"] for row in visible_rows] == [
        "fixture-host-a",
        "fixture-host-b",
    ]
    for identity, graph, row in zip(identities, graphs, visible_rows, strict=True):
        durable_key = next(iter(graph["tmux_panes"]))
        assert identity.stable_host_id in durable_key
        assert identity.display_hostname not in durable_key
        assert row["hostname"] == identity.display_hostname


def test_tmux_ai_status_is_host_local_and_carries_source_host(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    host_a = _host_identity("fixture-host-a", "lin1")
    host_b = _host_identity("fixture-host-b", "lin2")
    shared_legacy_status = tmp_path / "unqualified-tmux-AI-status.json"
    shared_legacy_acks = tmp_path / "unqualified-attention-acks.json"
    monkeypatch.setattr(common, "TMUX_AI_STATUS_PATH", shared_legacy_status)
    monkeypatch.setattr(common, "LEGACY_ATTENTION_ACKS_PATH", shared_legacy_acks)
    apps = (_status_app(host_a, tmp_path), _status_app(host_b, tmp_path))

    for webapp, identity in zip(apps, (host_a, host_b), strict=True):
        status = webapp.tmux_ai_status_empty()
        assert status["stable_host_id"] == identity.stable_host_id
        assert status["hostname"] == identity.display_hostname
        webapp._write_shared_tmux_ai_status_locked(status)

    expected_paths = {
        host_a.namespaced_path(tmp_path, "tmux-AI-status.json"),
        host_b.namespaced_path(tmp_path, "tmux-AI-status.json"),
    }
    assert all(path.exists() for path in expected_paths)
    assert not shared_legacy_status.exists()
    records = [json.loads(path.read_text(encoding="utf-8")) for path in expected_paths]
    assert {record["stable_host_id"] for record in records} == {
        host_a.stable_host_id,
        host_b.stable_host_id,
    }
    assert {record["hostname"] for record in records} == {
        host_a.display_hostname,
        host_b.display_hostname,
    }


def test_acknowledging_host_a_does_not_acknowledge_identical_host_b_pane() -> None:
    host_a = _host_identity("fixture-host-a", "lin1")
    host_b = _host_identity("fixture-host-b", "lin2")
    parts = ("agent-window", SESSION, WINDOW, TARGET, "claude", "interrupted", "same-alert")

    key_a = app_module.TmuxWebtermApp.attention_ack_key(*parts, host_identity=host_a)
    key_b = app_module.TmuxWebtermApp.attention_ack_key(*parts, host_identity=host_b)
    acknowledged = {key_a: 100.0}

    assert key_a != key_b
    assert host_a.stable_host_id in key_a
    assert host_b.stable_host_id in key_b
    assert host_a.display_hostname not in key_a
    assert host_b.display_hostname not in key_b
    assert key_a in acknowledged
    assert key_b not in acknowledged


def test_watch_root_interest_is_visible_only_to_its_source_host(tmp_path: Path) -> None:
    host_a = _host_identity("fixture-host-a", "lin1")
    host_b = _host_identity("fixture-host-b", "lin2")
    index_path = tmp_path / "watch-index.json"
    owner_a = app_module.SharedWatchRootIndex(
        index_path,
        owner_id="same-server-owner",
        host_identity=host_a,
        clock=lambda: 100.0,
    )
    owner_b = app_module.SharedWatchRootIndex(
        index_path,
        owner_id="same-server-owner",
        host_identity=host_b,
        clock=lambda: 100.0,
    )

    owner_a.update_active_roots({SESSION: "/fixture/repo-a"})

    assert owner_a.snapshot() == ["/fixture/repo-a"]
    assert owner_b.snapshot() == []
    assert owner_a.owner_path != owner_b.owner_path
    record_a = json.loads(owner_a.owner_path.read_text(encoding="utf-8"))
    assert record_a["stable_host_id"] == host_a.stable_host_id
    assert record_a["hostname"] == host_a.display_hostname
    assert host_b.stable_host_id not in json.dumps(record_a, sort_keys=True)


def test_activity_snapshots_and_heartbeat_rows_are_host_local(tmp_path: Path) -> None:
    host_a = _host_identity("fixture-host-a", "lin1")
    host_b = _host_identity("fixture-host-b", "lin2")
    ledgers = [
        ActivityLedger(
            tmp_path / "activity.json",
            heartbeat_path=tmp_path / "activity-heartbeats.jsonl",
            host_identity=identity,
        )
        for identity in (host_a, host_b)
    ]

    ledgers[0].heartbeat(SESSION, WINDOW, ts=100.0, byte_count=1)
    assert ledgers[1].snapshot() == {}
    ledgers[1].heartbeat(SESSION, WINDOW, ts=200.0, byte_count=2)

    for ledger, identity, timestamp in zip(ledgers, (host_a, host_b), (100.0, 200.0), strict=True):
        target_key = identity.qualify_key("activity", f"{SESSION}:{WINDOW}")
        snapshot = ledger.snapshot()
        assert target_key in snapshot
        assert snapshot[target_key]["stable_host_id"] == identity.stable_host_id
        assert snapshot[target_key]["hostname"] == identity.display_hostname
        assert snapshot[target_key]["last_user_input_ts"] == timestamp
        assert ledger.path == identity.namespaced_path(tmp_path, "activity.json")
        assert ledger.heartbeat_path == identity.namespaced_path(tmp_path, "activity-heartbeats.jsonl")
        rows = _json_lines(ledger.heartbeat_path)
        assert rows == [
            {
                "ts": timestamp,
                "s": SESSION,
                "w": WINDOW,
                "b": 1 if identity is host_a else 2,
                "src": "host",
                "stable_host_id": identity.stable_host_id,
                "hostname": identity.display_hostname,
            }
        ]
