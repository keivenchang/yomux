from __future__ import annotations

import json
import shutil
import time
import types
import uuid
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from pathlib import Path
from threading import Thread

import pytest

from test_mock_agents import case_command_name
from test_mock_agents import root_inventory_cases
from test_mock_agents import short_tmux_socket_path
from test_mock_agents import tmux_cmd
from test_mock_agents import wait_for_mockcase_render
from test_mock_agents import REPO_ROOT
from yolomux_lib import sessions as sessions_mod
from yolomux_lib import statusd
from yolomux_lib.app import TmuxWebtermApp
from yolomux_lib.statusd_client import StatusClient
from yolomux_lib.statusd_protocol import validate_inventory
from yolomux_lib.statusd_protocol import StatusSnapshotMetadata
from yolomux_lib.statusd_protocol import stamped_request
from yolomux_lib.tmux_utils import YOLOMUX_TMUX_SOCKET_ENV


class FakeStatusApp:
    builds = 0
    fail = False

    def __init__(self, sessions, **_kwargs):
        self.sessions = list(sessions)

    def build_auto_approve_status(self, *, timings, sync_workers):
        assert sync_workers is False
        FakeStatusApp.builds += 1
        if FakeStatusApp.fail:
            raise RuntimeError("unavailable")
        timings["discover_sessions"] = 0.0
        return {"session_order": list(self.sessions), "sessions": {}, "errors": [], "rules": {}}, 200


def test_statusd_reuses_one_encoded_snapshot_and_retains_stale_bytes(monkeypatch, tmp_path):
    FakeStatusApp.builds = 0
    FakeStatusApp.fail = False
    monkeypatch.setattr(statusd, "TmuxWebtermApp", FakeStatusApp)
    service = statusd.PersistentStatusService(tmp_path / "statusd.sock")
    request = {"action": "snapshot", "protocol_version": statusd.STATUSD_PROTOCOL_VERSION, "sessions": ["1"]}

    first, first_bytes = service.handle(request)
    second, second_bytes = service.handle(request)

    assert first["generation"] == second["generation"] == 1
    assert first_bytes == second_bytes
    assert FakeStatusApp.builds == 1
    service.handle({"action": "invalidate", "protocol_version": statusd.STATUSD_PROTOCOL_VERSION, "reason": "settings"})
    FakeStatusApp.fail = True
    stale, stale_bytes = service.handle(request)
    assert stale["stale"] is True
    assert stale_bytes == first_bytes
    assert service.status()["build_count"] == 1


def test_statusd_rebuilds_after_max_age_even_without_explicit_invalidate(monkeypatch, tmp_path):
    # Regression: a plain working->idle pane transition never calls invalidate() (no approval
    # prompt, no attention-ack), so without a bounded max age the snapshot built while an agent
    # was busy would be served forever and tab status dots would stay stuck on "running".
    FakeStatusApp.builds = 0
    FakeStatusApp.fail = False
    monkeypatch.setattr(statusd, "TmuxWebtermApp", FakeStatusApp)
    service = statusd.PersistentStatusService(tmp_path / "statusd.sock")
    request = {"action": "snapshot", "protocol_version": statusd.STATUSD_PROTOCOL_VERSION, "sessions": ["1"]}

    first, _ = service.handle(request)
    assert FakeStatusApp.builds == 1

    with service.lock:
        metadata, body = service.snapshot
        service.snapshot = (
            StatusSnapshotMetadata(metadata.generation, metadata.status, metadata.stale, metadata.built_at - 60.0),
            body,
        )

    second, _ = service.handle(request)
    assert FakeStatusApp.builds == 2
    assert second["generation"] == first["generation"] + 1


def _find_claude_case(case_name):
    for case in root_inventory_cases():
        data = case["data"]
        if str(data.get("agent") or "") == "claude" and str(data.get("case_name") or "") == case_name:
            return case
    raise AssertionError(f"no claude fixture case named {case_name!r}")


def test_statusd_dot_reflects_real_idle_pane_after_ttl_without_explicit_invalidate(monkeypatch, tmp_path):
    # End-to-end regression for the stuck-green-RUN-dot bug: a real mock Claude pane genuinely
    # transitions from working -> idle (no approval prompt, no attention-ack), which never calls
    # statusd.invalidate(). Before the fix, statusd would keep serving the "working" classification
    # forever. This drives real tmux + real agent_screen_state() classification, not a mock app.
    tmux_binary = shutil.which("tmux")
    if not tmux_binary:
        pytest.skip("tmux is not installed")
    working_case = _find_claude_case("working_visible_counter")
    idle_case = _find_claude_case("try_suggestion_idle")
    socket_path = short_tmux_socket_path("yostatusd")
    session = f"ymock-{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv(YOLOMUX_TMUX_SOCKET_ENV, str(socket_path))
    created = tmux_cmd(
        tmux_binary, socket_path, "new-session", "-d", "-s", session, "-x", "78", "-y", "35",
        f"cd {REPO_ROOT} && exec python3 tools/claude.py --mock",
    )
    assert created.returncode == 0, created.stderr or created.stdout
    try:
        rendered, pane = wait_for_mockcase_render(tmux_binary, socket_path, session, 'Try "fix typecheck errors"')
        assert rendered, pane

        tmux_cmd(tmux_binary, socket_path, "send-keys", "-t", f"{session}:", f"fixture {case_command_name(working_case)}", "Enter")
        rendered, pane = wait_for_mockcase_render(tmux_binary, socket_path, session, working_case["text"])
        assert rendered, f"pane never rendered working fixture:\n{pane}"

        service = statusd.PersistentStatusService(tmp_path / "statusd.sock")
        request = {"action": "snapshot", "protocol_version": statusd.STATUSD_PROTOCOL_VERSION, "sessions": [session]}
        service.handle(request)
        assert service.snapshot_payload["sessions"][session]["screen"]["key"] == "working"

        # The mock TUI ignores keystrokes while "working" (matching real Claude behavior of not
        # accepting input mid-turn), so switch fixtures by respawning the pane's process rather
        # than typing into a composer that isn't accepting input.
        respawned = tmux_cmd(
            tmux_binary, socket_path, "respawn-pane", "-k", "-t", f"{session}:",
            f"cd {REPO_ROOT} && exec python3 tools/claude.py --mock",
        )
        assert respawned.returncode == 0, respawned.stderr or respawned.stdout
        rendered, pane = wait_for_mockcase_render(tmux_binary, socket_path, session, 'Try "fix typecheck errors"')
        assert rendered, f"pane never re-rendered after respawn:\n{pane}"
        tmux_cmd(tmux_binary, socket_path, "send-keys", "-t", f"{session}:", f"fixture {case_command_name(idle_case)}", "Enter")
        rendered, pane = wait_for_mockcase_render(tmux_binary, socket_path, session, idle_case["text"])
        assert rendered, f"pane never rendered idle fixture:\n{pane}"

        # Immediately after the real pane went idle, with no invalidate() fired, statusd still
        # serves the stale "working" snapshot from before the fix's TTL kicks in.
        service.handle(request)
        assert service.snapshot_payload["sessions"][session]["screen"]["key"] == "working"

        with service.lock:
            metadata, body = service.snapshot
            service.snapshot = (
                StatusSnapshotMetadata(metadata.generation, metadata.status, metadata.stale, metadata.built_at - 60.0),
                body,
            )

        service.handle(request)
        assert service.snapshot_payload["sessions"][session]["screen"]["key"] == "idle"
    finally:
        tmux_cmd(tmux_binary, socket_path, "kill-server")
        shutil.rmtree(socket_path.parent, ignore_errors=True)


def test_real_tmux_agent_window_status_tabber_and_stats_share_lifecycle_identity(monkeypatch, tmp_path):
    """One real pane lifecycle must retain one statusd revision/identity across every consumer."""
    tmux_binary = shutil.which("tmux")
    if not tmux_binary:
        pytest.skip("tmux is not installed")
    working_case = _find_claude_case("working_visible_counter")
    idle_case = _find_claude_case("try_suggestion_idle")
    socket_path = short_tmux_socket_path("yoagent-window")
    status_socket = tmp_path / "statusd.sock"
    session = f"ymock-{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv(YOLOMUX_TMUX_SOCKET_ENV, str(socket_path))
    created = tmux_cmd(
        tmux_binary, socket_path, "new-session", "-d", "-s", session, "-x", "78", "-y", "35",
        f"cd {REPO_ROOT} && exec python3 tools/claude.py --mock",
    )
    assert created.returncode == 0, created.stderr or created.stdout
    service = statusd.PersistentStatusService(status_socket, idle_seconds=60.0)
    service_thread = Thread(target=service.run, daemon=True)
    service_thread.start()
    client = StatusClient(status_socket)

    def configured_app():
        app = TmuxWebtermApp([session], status_service_mode=True)
        app.status_client = client
        app.notification_transition_seconds = lambda: 30.0
        app.cached_session_files_payloads_for_infos = lambda infos, hours=24.0: {name: {"files": [], "repos": []} for name in infos}
        app.compute_tabber_activity_rows_via_jobd = lambda infos, **_kwargs: {name: {"agents": [], "agent_windows": []} for name in infos}
        return app

    def snapshot():
        response, body = client.snapshot([session])
        assert response.get("ok") is True and body
        return response, json.loads(body.decode("utf-8"))

    def identity_rows(payload):
        return {
            (name, str(row.get("window_index")), str(row.get("pane_target")), str(row.get("kind"))): str(row.get("state"))
            for name, record in payload.get("sessions", {}).items()
            if isinstance(record, dict)
            for row in record.get("agent_windows", [])
            if isinstance(row, dict)
        }

    def stats_attempt(scheduled_at):
        return types.SimpleNamespace(
            epoch_id="test:agent-status:1",
            epoch_started_at=scheduled_at,
            scheduled_at=scheduled_at,
            cadence_seconds=10,
            owner_generation=1,
        )

    try:
        rendered, pane = wait_for_mockcase_render(tmux_binary, socket_path, session, 'Try "fix typecheck errors"')
        assert rendered, pane
        tmux_cmd(tmux_binary, socket_path, "send-keys", "-t", f"{session}:", f"fixture {case_command_name(working_case)}", "Enter")
        rendered, pane = wait_for_mockcase_render(tmux_binary, socket_path, session, working_case["text"])
        assert rendered, pane

        working_response, working_payload = snapshot()
        app = configured_app()
        working_activity = app.build_activity_payload()
        working_rows = identity_rows(working_payload)
        assert working_rows and set(working_rows) == {
            (session, str(row.get("window_index")), str(row.get("pane_target")), str(row.get("kind")))
            for row in working_activity["agent_windows"][session]
        }
        assert working_activity["agent_window_snapshot_revision"] == working_response["generation"]
        working_stats = app.collect_current_stats_agent_status(stats_attempt(time.time())).observations[0].payload
        assert set(working_stats["states"]) == {"|".join(key) for key in working_rows}
        assert set(working_stats["states"].values()) == {"run"}

        respawned = tmux_cmd(
            tmux_binary, socket_path, "respawn-pane", "-k", "-t", f"{session}:",
            f"cd {REPO_ROOT} && exec python3 tools/claude.py --mock",
        )
        assert respawned.returncode == 0, respawned.stderr or respawned.stdout
        rendered, pane = wait_for_mockcase_render(tmux_binary, socket_path, session, 'Try "fix typecheck errors"')
        assert rendered, pane
        tmux_cmd(tmux_binary, socket_path, "send-keys", "-t", f"{session}:", f"fixture {case_command_name(idle_case)}", "Enter")
        rendered, pane = wait_for_mockcase_render(tmux_binary, socket_path, session, idle_case["text"])
        assert rendered, pane

        with service.lock:
            metadata, body = service.snapshot
            service.snapshot = (StatusSnapshotMetadata(metadata.generation, metadata.status, metadata.stale, metadata.built_at - 60.0), body)
        idle_response, idle_payload = snapshot()
        assert idle_response["generation"] > working_response["generation"]
        idle_rows = identity_rows(idle_payload)
        assert set(idle_rows) == set(working_rows)
        assert set(idle_rows.values()) == {"idle"}
        idle_activity = app.build_activity_payload()
        assert {
            (session, str(row.get("window_index")), str(row.get("pane_target")), str(row.get("kind")))
            for row in idle_activity["agent_windows"][session]
        } == set(idle_rows)
        assert idle_activity["agent_window_snapshot_revision"] == idle_response["generation"]
        stopped_at = max(float(row.get("working_stopped_ts") or 0.0) for record in idle_payload["sessions"].values() for row in record["agent_windows"])
        assert set(app.collect_current_stats_agent_status(stats_attempt(stopped_at + 1.0)).observations[0].payload["states"].values()) == {"transition"}
        assert set(app.collect_current_stats_agent_status(stats_attempt(stopped_at + 31.0)).observations[0].payload["states"].values()) == {"idle"}

        killed = tmux_cmd(tmux_binary, socket_path, "kill-session", "-t", session)
        assert killed.returncode == 0, killed.stderr or killed.stdout
        client.invalidate("tmux-topology")
        _removed_response, removed_payload = snapshot()
        assert identity_rows(removed_payload) == {}
        assert app.build_activity_payload()["agent_windows"] == {}
        refreshed_app = configured_app()
        assert refreshed_app.build_activity_payload()["agent_windows"] == {}
    finally:
        client.request({"action": "shutdown"})
        service_thread.join(timeout=2.0)
        tmux_cmd(tmux_binary, socket_path, "kill-server")
        shutil.rmtree(socket_path.parent, ignore_errors=True)
def test_statusd_snapshot_body_carries_the_metadata_generation_for_full_and_session_reads(monkeypatch, tmp_path):
    class SessionStatusApp(FakeStatusApp):
        def build_auto_approve_status(self, *, timings, sync_workers):
            assert sync_workers is False
            timings["discover_sessions"] = 0.0
            return {"session_order": list(self.sessions), "sessions": {"1": {"agent_windows": []}}, "errors": [], "rules": {}}, 200

    monkeypatch.setattr(statusd, "TmuxWebtermApp", SessionStatusApp)
    service = statusd.PersistentStatusService(tmp_path / "statusd.sock")
    request = {"action": "snapshot", "protocol_version": statusd.STATUSD_PROTOCOL_VERSION, "sessions": ["1"]}

    metadata, body = service.handle(request)
    session_metadata, session_body = service.handle({**request, "session": "1"})

    assert json.loads(body)["agent_window_snapshot_revision"] == metadata["generation"] == 1
    assert json.loads(session_body)["agent_window_snapshot_revision"] == session_metadata["generation"] == 1


def test_statusd_rejects_invalid_session_input_without_building(monkeypatch, tmp_path):
    FakeStatusApp.builds = 0
    monkeypatch.setattr(statusd, "TmuxWebtermApp", FakeStatusApp)
    service = statusd.PersistentStatusService(Path(tmp_path / "statusd.sock"))

    response, body = service.handle({"action": "snapshot", "protocol_version": statusd.STATUSD_PROTOCOL_VERSION, "sessions": ["1", 2]})

    assert response["ok"] is False
    assert body == b""
    assert FakeStatusApp.builds == 0


def test_statusd_concurrent_demand_builds_one_shared_generation(monkeypatch, tmp_path):
    FakeStatusApp.builds = 0
    FakeStatusApp.fail = False
    monkeypatch.setattr(statusd, "TmuxWebtermApp", FakeStatusApp)
    service = statusd.PersistentStatusService(tmp_path / "statusd.sock")
    request = {"action": "snapshot", "protocol_version": statusd.STATUSD_PROTOCOL_VERSION, "sessions": ["1"]}

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(service.handle, [request, request]))

    assert FakeStatusApp.builds == 1
    assert results[0][0]["generation"] == results[1][0]["generation"] == 1
    assert results[0][1] == results[1][1]


def test_two_clients_share_one_statusd_pid_and_encoded_generation(monkeypatch, tmp_path):
    FakeStatusApp.builds = 0
    FakeStatusApp.fail = False
    monkeypatch.setattr(statusd, "TmuxWebtermApp", FakeStatusApp)
    socket_path = tmp_path / "services" / "statusd.sock"
    service = statusd.PersistentStatusService(socket_path, idle_seconds=60.0)
    thread = Thread(target=service.run, daemon=True)
    thread.start()
    first = StatusClient(socket_path)
    second = StatusClient(socket_path)
    try:
        assert first.ensure_started() is True
        assert second.ensure_started() is True
        first_response, first_body = first.snapshot(["1"])
        second_response, second_body = second.snapshot(["1"])
        first_pid = first.request({"action": "status"}).get("pid")
        second_pid = second.request({"action": "status"}).get("pid")
    finally:
        first.request({"action": "shutdown"})
        thread.join(timeout=2.0)

    assert first_response["generation"] == second_response["generation"] == 1
    assert first_body == second_body
    assert first_pid == second_pid
    assert FakeStatusApp.builds == 1
    assert thread.is_alive() is False


def test_statusd_generation_wait_does_not_starve_snapshot_or_invalidate(monkeypatch, tmp_path):
    """A long generation wait must not monopolize statusd's Unix listener."""
    FakeStatusApp.builds = 0
    FakeStatusApp.fail = False
    monkeypatch.setattr(statusd, "TmuxWebtermApp", FakeStatusApp)
    socket_path = tmp_path / "services" / "statusd.sock"
    service = statusd.PersistentStatusService(socket_path, idle_seconds=60.0)
    thread = Thread(target=service.run, daemon=True)
    thread.start()
    client = StatusClient(socket_path)
    try:
        assert client.ensure_started() is True
        initial, _body = client.snapshot(["1"])
        lease = client.acquire_generation_lease()
        assert lease["ok"] is True
        with ThreadPoolExecutor(max_workers=1) as executor:
            waiting = executor.submit(client.wait_generation, initial["generation"], 2.0)
            time.sleep(0.05)
            assert client.invalidate("test") ["ok"] is True
            # This snapshot would time out with the former serial listener while
            # the waiter owns the only accepted connection.
            refreshed, _body = client.snapshot(["1"], timeout=1.0)
            waited = waiting.result(timeout=2.0)
        assert refreshed["generation"] > initial["generation"]
        assert waited["changed"] is True
        assert waited["generation"] == refreshed["generation"]
    finally:
        if 'lease' in locals() and lease.get("lease_id"):
            client.release_generation_lease(lease["lease_id"])
        client.request({"action": "shutdown"})
        thread.join(timeout=2.0)

    assert thread.is_alive() is False


def test_statusd_refresh_worker_does_no_build_without_a_generation_lease(monkeypatch, tmp_path):
    FakeStatusApp.builds = 0
    FakeStatusApp.fail = False
    monkeypatch.setattr(statusd, "TmuxWebtermApp", FakeStatusApp)
    service = statusd.PersistentStatusService(tmp_path / "statusd.sock")
    service.start_refresh_worker()
    try:
        time.sleep(0.15)
        assert FakeStatusApp.builds == 0
    finally:
        service.stop_event.set()
        with service.lock:
            service.lock.notify_all()
        worker = service.refresh_worker
        if worker is not None:
            worker.join(timeout=1.0)


def test_statusd_idle_reaps_dead_client_leases(monkeypatch, tmp_path):
    service = statusd.PersistentStatusService(tmp_path / "statusd.sock", idle_seconds=1.0)
    service.leases["dead-client"] = 12345
    service.last_client_at = time.monotonic() - 2.0
    reaped = []

    def fake_reap(leases):
        reaped.extend(leases)
        leases.clear()
        return 1

    monkeypatch.setattr(statusd, "reap_dead_client_leases", fake_reap)

    assert service.idle_due() is True
    assert reaped == ["dead-client"]
    assert service.leases == {}


def test_statusd_listener_exits_after_reaping_an_abandoned_lease(tmp_path):
    socket_path = tmp_path / "services" / "statusd.sock"
    service = statusd.PersistentStatusService(socket_path, idle_seconds=1.0)
    thread = Thread(target=service.run, daemon=True)
    thread.start()
    client = StatusClient(socket_path)
    try:
        assert client.ensure_started() is True
        lease = client.request(stamped_request("lease", client_pid=999_999_999))
        assert lease["ok"] is True
        thread.join(timeout=2.5)
        assert thread.is_alive() is False
    finally:
        if thread.is_alive():
            client.request({"action": "shutdown"})
            thread.join(timeout=2.0)


def test_two_web_apps_forward_one_shared_statusd_snapshot_without_local_build(monkeypatch, tmp_path):
    FakeStatusApp.builds = 0
    FakeStatusApp.fail = False
    monkeypatch.setattr(statusd, "TmuxWebtermApp", FakeStatusApp)
    socket_path = tmp_path / "services" / "statusd.sock"
    service = statusd.PersistentStatusService(socket_path, idle_seconds=60.0)
    thread = Thread(target=service.run, daemon=True)
    thread.start()
    first_app = TmuxWebtermApp(["1"])
    second_app = TmuxWebtermApp(["1"])
    first_app.status_client = StatusClient(socket_path)
    second_app.status_client = StatusClient(socket_path)
    # Isolate the shared-snapshot forwarding invariant from the read-path attention-ack
    # merge (which legitimately invalidates statusd when a peer ack is pending). With no
    # peer acks the merge is a no-op, so both reads share one daemon build.
    monkeypatch.setattr(first_app, "merge_shared_attention_acks", lambda: False)
    monkeypatch.setattr(second_app, "merge_shared_attention_acks", lambda: False)
    monkeypatch.setattr(first_app, "build_auto_approve_status", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("first web app built status")))
    monkeypatch.setattr(second_app, "build_auto_approve_status", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("second web app built status")))
    try:
        first_body, first_status = first_app.auto_approve_status_bytes()
        second_body, second_status = second_app.auto_approve_status_bytes()
    finally:
        first_app.control_server.stop()
        second_app.control_server.stop()
        first_app.status_client.request({"action": "shutdown"})
        thread.join(timeout=2.0)

    assert first_status == second_status == 200
    assert first_body == second_body
    assert FakeStatusApp.builds == 1
    assert thread.is_alive() is False


def _fake_status_info(session, cwd, kind="claude"):
    pane = types.SimpleNamespace(target=f"{session}:0.0", window="0", pane="0", current_path=cwd, active=True)
    agent = types.SimpleNamespace(kind=kind, pane_target=f"{session}:0.0")
    return types.SimpleNamespace(session=session, panes=[pane], agents=[agent])


def test_statusd_inventory_discovers_daemon_roster_and_bumps_generation_only_on_change(monkeypatch, tmp_path):
    # The daemon owns the roster: even though the web hint says ["ignored"], the
    # inventory must reflect the tmux-enumerated roster the daemon discovers itself.
    monkeypatch.setattr(statusd, "list_tmux_session_names", lambda: (["alpha"], None))
    state = {"cwd": "/repoA"}

    def fake_discover(names, enrich_paths=True):
        assert names == ["alpha"], "daemon roster must win over the web hint"
        return ({"alpha": _fake_status_info("alpha", state["cwd"])}, [])

    monkeypatch.setattr(sessions_mod, "discover_sessions", fake_discover)
    service = statusd.PersistentStatusService(tmp_path / "statusd.sock")

    meta1, body1 = service.handle({"action": "inventory", "protocol_version": statusd.STATUSD_PROTOCOL_VERSION, "sessions": ["ignored"]})
    payload1 = json.loads(body1)
    assert payload1["roster"] == ["alpha"] and payload1["roster_source"] == "daemon"
    assert payload1["sessions"]["alpha"]["source_signature"]
    validate_inventory(meta1, body1)
    gen1 = meta1["inventory_generation"]

    # Unchanged topology reuses the same generation.
    meta2, _ = service.handle({"action": "inventory", "protocol_version": statusd.STATUSD_PROTOCOL_VERSION})
    assert meta2["inventory_generation"] == gen1

    # A pane cwd change bumps the source signature and the inventory generation.
    state["cwd"] = "/repoB"
    meta3, _ = service.handle({"action": "inventory", "protocol_version": statusd.STATUSD_PROTOCOL_VERSION})
    assert meta3["inventory_generation"] == gen1 + 1


def test_statusd_inventory_uses_lightweight_discovery_without_path_enrichment(monkeypatch, tmp_path):
    monkeypatch.setattr(statusd, "list_tmux_session_names", lambda: (["alpha"], None))
    enrich_calls = []

    def fake_discover(names, enrich_paths=True):
        enrich_calls.append(enrich_paths)
        return ({"alpha": _fake_status_info("alpha", "/repoA")}, [])

    monkeypatch.setattr(sessions_mod, "discover_sessions", fake_discover)
    service = statusd.PersistentStatusService(tmp_path / "statusd.sock")

    meta, _ = service.handle({"action": "inventory", "protocol_version": statusd.STATUSD_PROTOCOL_VERSION})

    assert meta["ok"] is True
    # The status/inventory path must never trigger heavy path enrichment.
    assert enrich_calls == [False]


def test_statusd_inventory_falls_back_to_web_hint_when_tmux_enumeration_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(statusd, "list_tmux_session_names", lambda: ([], "tmux error"))

    def fake_discover(names, enrich_paths=True):
        assert names == ["hinted"]
        return ({"hinted": _fake_status_info("hinted", "/repoA")}, [])

    monkeypatch.setattr(sessions_mod, "discover_sessions", fake_discover)
    service = statusd.PersistentStatusService(tmp_path / "statusd.sock")

    _meta, body = service.handle({"action": "inventory", "protocol_version": statusd.STATUSD_PROTOCOL_VERSION, "sessions": ["hinted"]})
    payload = json.loads(body)

    assert payload["roster"] == ["hinted"] and payload["roster_source"] == "hint"


def test_web_status_byte_forwarder_never_calls_in_process_status_builder(monkeypatch):
    app = TmuxWebtermApp(["1"])
    encoded = b'{"session_order":["1"],"sessions":{}}'
    monkeypatch.setattr(app.status_client, "snapshot", lambda sessions, session=None, timeout=1.0: ({"ok": True, "protocol_version": statusd.STATUSD_PROTOCOL_VERSION, "generation": 7, "status": 200, "stale": False, "built_at": 1.0}, encoded))
    monkeypatch.setattr(app, "build_auto_approve_status", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("web must not build status")))
    try:
        body, status = app.auto_approve_status_bytes()
    finally:
        app.control_server.stop()

    assert body is encoded
    assert status == 200


def test_web_read_returns_service_unavailable_without_building_when_statusd_down(monkeypatch):
    # Case A: statusd reports unavailable. The web read must forward a structured 503 and never fall
    # back to building status in the web process.
    app = TmuxWebtermApp(["1"])
    monkeypatch.setattr(app, "merge_shared_attention_acks", lambda: False)
    monkeypatch.setattr(app.status_client, "snapshot", lambda sessions, session=None, timeout=1.0: ({"ok": False, "status": int(HTTPStatus.SERVICE_UNAVAILABLE), "error": "unavailable"}, b""))
    monkeypatch.setattr(app, "build_auto_approve_status", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("web must not build status when statusd is down")))
    try:
        body, status = app.auto_approve_status_bytes()
    finally:
        app.control_server.stop()

    assert status == HTTPStatus.SERVICE_UNAVAILABLE
    assert body == b'{"error":"status service unavailable"}'


def test_web_read_forwards_stale_bytes_when_statusd_build_fails_after_invalidation(monkeypatch, tmp_path):
    # Case B: a real statusd builds once, is invalidated, then its next build fails. The web read must
    # forward the retained stale bytes (stale=True) without the web process building anything, and
    # statusd's successful build_count must stay at 1.
    FakeStatusApp.builds = 0
    FakeStatusApp.fail = False
    monkeypatch.setattr(statusd, "TmuxWebtermApp", FakeStatusApp)
    socket_path = tmp_path / "services" / "statusd.sock"
    service = statusd.PersistentStatusService(socket_path, idle_seconds=60.0)
    thread = Thread(target=service.run, daemon=True)
    thread.start()
    web_app = TmuxWebtermApp(["1"])
    web_app.status_client = StatusClient(socket_path)
    monkeypatch.setattr(web_app, "merge_shared_attention_acks", lambda: False)
    monkeypatch.setattr(web_app, "build_auto_approve_status", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("web must not build status")))
    try:
        fresh_body, fresh_status = web_app.auto_approve_status_bytes()
        assert fresh_status == 200
        assert service.status()["build_count"] == 1

        web_app.status_client.invalidate("auto_approve")
        FakeStatusApp.fail = True

        response, raw_body = web_app.status_client.snapshot(["1"])
        stale_body, stale_status = web_app.auto_approve_status_bytes()
    finally:
        web_app.control_server.stop()
        web_app.status_client.request({"action": "shutdown"})
        thread.join(timeout=2.0)

    assert response["ok"] is True
    assert response["stale"] is True
    assert raw_body == fresh_body
    assert stale_status == 200
    assert stale_body == fresh_body
    assert service.status()["build_count"] == 1
    assert thread.is_alive() is False
