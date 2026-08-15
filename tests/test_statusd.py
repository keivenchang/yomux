from __future__ import annotations

import errno
import json
import shutil
import time
import types
import uuid
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from pathlib import Path
from threading import Event
from threading import Thread

import pytest

from tests.helpers.mock_agents import case_command_name
from tests.helpers.mock_agents import root_inventory_cases
from tests.helpers.mock_agents import short_tmux_socket_path
from tests.helpers.mock_agents import tmux_cmd
from tests.helpers.mock_agents import wait_for_mockcase_render
from tests.helpers.mock_agents import REPO_ROOT
from yolomux_lib import control as control_module
from yolomux_lib import sessions as sessions_mod
from yolomux_lib import statusd
from yolomux_lib import statusd_client
from yolomux_lib.local_services import client as local_service_client_module
from yolomux_lib.local_services import rpc
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

    def build_auto_approve_status(self, *, timings, sync_workers, **_kwargs):
        assert sync_workers is False
        FakeStatusApp.builds += 1
        if FakeStatusApp.fail:
            raise RuntimeError("unavailable")
        timings["discover_sessions"] = 0.0
        return {"session_order": list(self.sessions), "sessions": {}, "errors": [], "rules": {}}, 200


class RosterStatusApp(FakeStatusApp):
    """A status app whose snapshot payload actually carries its roster's sessions.

    ``FakeStatusApp`` always builds ``sessions: {}``, so it cannot distinguish "this snapshot does
    not cover that session" from "that session does not exist" -- which is exactly the distinction
    the session-scoped read gets wrong.
    """

    gate: Event | None = None
    entered: Event | None = None

    def build_auto_approve_status(self, *, timings, sync_workers, **_kwargs):
        assert sync_workers is False
        FakeStatusApp.builds += 1
        timings["discover_sessions"] = 0.0
        if RosterStatusApp.entered is not None:
            RosterStatusApp.entered.set()
        if RosterStatusApp.gate is not None:
            assert RosterStatusApp.gate.wait(timeout=10)
        return {
            "session_order": list(self.sessions),
            "sessions": {name: {"session": name, "enabled": False} for name in self.sessions},
            "errors": [],
            "rules": {},
        }, 200


def _statusd_snapshot_request(sessions, session=None):
    request = {
        "action": "snapshot",
        "protocol_version": statusd.STATUSD_PROTOCOL_VERSION,
        "sessions": list(sessions),
    }
    if session is not None:
        request["session"] = session
    return request


def test_statusd_session_read_never_reports_not_found_from_another_rosters_snapshot(monkeypatch, tmp_path):
    # Regression: creating a session grew the web roster, statusd bound `session_names` to the new
    # roster the moment the rebuild STARTED, and every read in that window was answered from the
    # previous roster's snapshot. The unscoped read claimed `stale: False` and the session-scoped
    # read returned a definitive 404 `unknown session` for a session that existed -- which the
    # browser's per-session auto-approve fallback turned into a logged server error one second
    # after create-session. Both directions are asserted: a session the retained snapshot really
    # does cover but does not contain must still be reported as unknown.
    FakeStatusApp.builds = 0
    FakeStatusApp.fail = False
    RosterStatusApp.gate = None
    RosterStatusApp.entered = None
    monkeypatch.setattr(statusd, "TmuxWebtermApp", RosterStatusApp)
    service = statusd.PersistentStatusService(tmp_path / "statusd.sock")
    try:
        wait_for_direct_snapshot(service, _statusd_snapshot_request(["old"]))
        assert service.handle(_statusd_snapshot_request(["old"], session="old"))[0]["ok"] is True

        RosterStatusApp.gate = Event()
        RosterStatusApp.entered = Event()
        service.handle(_statusd_snapshot_request(["old", "new"]))
        assert RosterStatusApp.entered.wait(timeout=5), "the roster rebuild never started"

        during_scoped, during_scoped_body = service.handle(_statusd_snapshot_request(["old", "new"], session="new"))
        during_unscoped, _ = service.handle(_statusd_snapshot_request(["old", "new"]))
        RosterStatusApp.gate.set()

        assert during_scoped == {"ok": False, "status": HTTPStatus.SERVICE_UNAVAILABLE, "error": "refreshing"}
        assert during_scoped_body == b""
        assert during_unscoped == {"ok": False, "status": HTTPStatus.SERVICE_UNAVAILABLE, "error": "refreshing"}

        deadline = time.monotonic() + 5.0
        while True:
            settled, settled_body = service.handle(_statusd_snapshot_request(["old", "new"], session="new"))
            if settled.get("ok") is True or time.monotonic() >= deadline:
                break
            time.sleep(0.02)
        assert settled["ok"] is True and json.loads(settled_body)["session"] == "new", (settled, settled_body)
        # The snapshot now genuinely covers this roster, so a name it does not contain is unknown.
        assert service.handle(_statusd_snapshot_request(["old", "new"], session="ghost"))[0] == {
            "ok": False,
            "status": HTTPStatus.NOT_FOUND,
            "error": "unknown session",
        }
    finally:
        RosterStatusApp.gate = None
        RosterStatusApp.entered = None
        service.stop_event.set()
        with service.lock:
            service.lock.notify_all()


def wait_for_client_snapshot(client, sessions, session=None, timeout=2.0):
    response, body = client.snapshot(list(sessions), session=session, timeout=min(timeout, 0.25))
    if response.get("ok") is True and response.get("stale") is not True:
        return response, body
    if response.get("ok") is True:
        after_generation = int(response.get("generation") or 0)
    else:
        assert response == {"ok": False, "status": HTTPStatus.SERVICE_UNAVAILABLE, "error": "refreshing"}
        after_generation = 0
    generation = client.wait_generation(after_generation, timeout)
    assert generation.get("changed") is True
    response, body = client.snapshot(list(sessions), session=session, timeout=min(timeout, 0.25))
    assert response.get("ok") is True and body
    return response, body


def wait_for_direct_snapshot(service, request, timeout=2.0):
    service.start_refresh_worker()
    response, body = service.handle(request)
    if response.get("ok") is True:
        return response, body
    assert response == {"ok": False, "status": HTTPStatus.SERVICE_UNAVAILABLE, "error": "refreshing"}
    deadline = time.monotonic() + timeout
    with service.lock:
        while service.snapshot is None:
            remaining = deadline - time.monotonic()
            assert remaining > 0
            service.lock.wait(remaining)
    response, body = service.handle(request)
    assert response.get("ok") is True and body
    return response, body


def test_status_generation_probe_is_immediate_within_the_existing_transport_budget(monkeypatch):
    client = object.__new__(StatusClient)
    calls = []
    monkeypatch.setattr(
        client,
        "request",
        lambda payload, timeout: calls.append((payload, timeout)) or {"ok": True, "changed": False, "generation": 7},
    )

    response = client.probe_generation(7)

    assert response == {"ok": True, "changed": False, "generation": 7}
    assert calls == [(
        stamped_request("wait_generation", after_generation=7, timeout_seconds=0.0),
        statusd_client.STATUSD_GENERATION_PROBE_TRANSPORT_TIMEOUT_SECONDS,
    )]
    assert statusd_client.STATUSD_GENERATION_PROBE_TRANSPORT_TIMEOUT_SECONDS == 1.5


def test_statusd_reuses_one_encoded_snapshot_and_retains_stale_bytes(monkeypatch, tmp_path):
    FakeStatusApp.builds = 0
    FakeStatusApp.fail = False
    monkeypatch.setattr(statusd, "TmuxWebtermApp", FakeStatusApp)
    service = statusd.PersistentStatusService(tmp_path / "statusd.sock")
    request = {"action": "snapshot", "protocol_version": statusd.STATUSD_PROTOCOL_VERSION, "sessions": ["1"]}

    first, first_bytes = wait_for_direct_snapshot(service, request)
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
    service.stop_event.set()
    with service.lock:
        service.lock.notify_all()


def test_statusd_activity_summary_returns_exact_daemon_assembled_json(monkeypatch, tmp_path, legacy_activity_summary_enabled):
    calls = []
    expected = {"generated_at": "now", "session_order": ["1"], "sessions": {"1": {"session": "1"}}}

    class ActivityStatusApp(FakeStatusApp):
        def __init__(self, sessions, **kwargs):
            super().__init__(sessions, **kwargs)
            self.yoagent_controller = types.SimpleNamespace(
                load_yoagent_session_summaries=lambda: calls.append("load_summaries")
            )

        def assemble_activity_summary_payload(self, **kwargs):
            kwargs.pop("timings")
            calls.append(kwargs)
            return expected

    monkeypatch.setattr(statusd, "TmuxWebtermApp", ActivityStatusApp)
    service = statusd.PersistentStatusService(tmp_path / "statusd.sock")
    request_body = json.dumps({"1": {"git": {"branch": "main"}}}).encode("utf-8")
    response, body = service.handle(
        stamped_request(
            "activity_summary",
            sessions=["1"],
            force=True,
            locale="en",
            session_scope="all",
            hours=336.0,
            work_by_session_binary=True,
        ),
        request_body,
    )

    assert response["ok"] is True
    assert response["status"] == HTTPStatus.OK
    assert json.loads(body) == expected
    assert service.app is None
    assert service.activity_app is not None
    assert calls == ["load_summaries", {
        "force": True,
        "locale": "en",
        "session_scope": "all",
        "hours": 336.0,
        "work_by_session": {"1": {"git": {"branch": "main"}}},
    }]
    profile = service.status()["activity_summary"]
    assert {
        "in_progress": profile["in_progress"],
        "phase": profile["phase"],
        "sessions": profile["sessions"],
        "work_sessions": profile["work_sessions"],
        "request_bytes": profile["request_bytes"],
        "error": profile["error"],
        "response_bytes": profile["response_bytes"],
    } == {
        "in_progress": False,
        "phase": "complete",
        "sessions": 1,
        "work_sessions": 1,
        "request_bytes": len(request_body),
        "error": "",
        "response_bytes": len(body),
    }
    assert set(profile["timings"]) == {"decode_ms", "load_summaries_ms", "encode_ms"}
    assert all(value >= 0 for value in profile["timings"].values())
    assert profile["total_ms"] >= 0


def test_status_client_activity_summary_sends_work_projection_in_bounded_binary_frame(monkeypatch, tmp_path, legacy_activity_summary_enabled):
    client = StatusClient(tmp_path / "statusd.sock")
    calls = []
    monkeypatch.setattr(client, "ensure_started", lambda: True)
    monkeypatch.setattr(
        client,
        "request_with_binary",
        lambda request, timeout, request_binary: calls.append((request, timeout, request_binary)) or (
            {"ok": True},
            b'{"session_order":[]}',
        ),
    )
    work = {"1": {"git": {"branch": "main"}}}

    response, body = client.activity_summary(
        ["1"],
        force=False,
        locale="en",
        session_scope="configured",
        hours=24.0,
        work_by_session=work,
    )

    assert response == {"ok": True}
    assert body == b'{"session_order":[]}'
    request, timeout, request_binary = calls[0]
    assert request["work_by_session_binary"] is True
    assert "work_by_session" not in request
    assert timeout == 60.0
    assert json.loads(request_binary) == work


def test_local_service_client_preserves_request_binary_across_socket_recovery(monkeypatch, tmp_path):
    client = StatusClient(tmp_path / "statusd.sock")
    attempts = []

    def request_after_restart(*_args, **kwargs):
        attempts.append(kwargs["binary"])
        if len(attempts) == 1:
            raise FileNotFoundError(errno.ENOENT, "statusd socket absent")
        return {"ok": True}, b"response"

    monkeypatch.setattr(local_service_client_module, "local_service_request", request_after_restart)
    monkeypatch.setattr(client.registry, "ensure_started", lambda: True)

    response, body = client.request_with_binary(
        {"action": "activity_summary"},
        request_binary=b"request",
    )

    assert response == {"ok": True}
    assert body == b"response"
    assert attempts == [b"request", b"request"]


def test_statusd_activity_summary_request_rejects_unbounded_or_unknown_work():
    valid = {
        "sessions": ["1"],
        "force": False,
        "locale": "en",
        "session_scope": "configured",
        "hours": 24.0,
        "work_by_session_binary": True,
    }
    invalid = (
        {**valid, "sessions": ["1", "1"]},
        {**valid, "hours": 337.0},
        {**valid, "work_by_session_binary": False},
    )

    for fields in invalid:
        with pytest.raises(statusd.StatusProtocolError):
            stamped_request("activity_summary", **fields)


@pytest.mark.parametrize(
    ("body", "error"),
    (
        pytest.param(b"{", "activity work body must be JSON", id="malformed-json"),
        pytest.param(b'{"2":{}}', "invalid activity work session", id="unknown-session"),
        pytest.param(
            b'{"1":{"content":"private"}}',
            "invalid activity work field",
            id="unknown-work-field",
        ),
        pytest.param(
            b"x" * (rpc.LOCAL_RPC_MAX_BINARY_BYTES + 1),
            "activity work body too large",
            id="oversized-body",
        ),
    ),
)
def test_statusd_activity_summary_rejects_invalid_binary_work_body(monkeypatch, tmp_path, body, error, legacy_activity_summary_enabled):
    monkeypatch.setattr(statusd, "TmuxWebtermApp", FakeStatusApp)
    service = statusd.PersistentStatusService(tmp_path / "statusd.sock")
    response, response_body = service.handle(
        stamped_request(
            "activity_summary",
            sessions=["1"],
            force=False,
            locale="en",
            session_scope="configured",
            hours=24.0,
            work_by_session_binary=True,
        ),
        body,
    )

    assert response == {"ok": False, "status": HTTPStatus.BAD_REQUEST, "error": error}
    assert response_body == b""


def test_statusd_rebuilds_after_max_age_without_bumping_an_unchanged_snapshot(monkeypatch, tmp_path):
    # Regression: a plain working->idle pane transition never calls invalidate() (no approval
    # prompt, no attention-ack), so without a bounded max age the snapshot built while an agent
    # was busy would be served forever and tab status dots would stay stuck on "running".
    FakeStatusApp.builds = 0
    FakeStatusApp.fail = False
    monkeypatch.setattr(statusd, "TmuxWebtermApp", FakeStatusApp)
    service = statusd.PersistentStatusService(tmp_path / "statusd.sock")
    request = {"action": "snapshot", "protocol_version": statusd.STATUSD_PROTOCOL_VERSION, "sessions": ["1"]}

    first, _ = wait_for_direct_snapshot(service, request)
    assert FakeStatusApp.builds == 1

    with service.lock:
        metadata, body = service.snapshot
        service.snapshot = (
            StatusSnapshotMetadata(metadata.generation, metadata.status, metadata.stale, metadata.built_at - 60.0),
            body,
        )

    second, _ = service.handle(request)
    deadline = time.monotonic() + 2.0
    with service.lock:
        while FakeStatusApp.builds < 2:
            remaining = deadline - time.monotonic()
            assert remaining > 0
            service.lock.wait(remaining)
    assert FakeStatusApp.builds == 2
    assert second["generation"] == first["generation"]
    assert service.status()["build_count"] == 1
    service.stop_event.set()
    with service.lock:
        service.lock.notify_all()


def test_statusd_cold_session_uses_slow_capture_tier_and_activity_promotes_immediately(monkeypatch, tmp_path):
    wall = [200_000.0]
    monotonic = [1_000.0]
    activity = {"cold": int(wall[0] - 90_000)}
    captured: list[set[str]] = []

    class CadenceStatusApp(FakeStatusApp):
        revision = 0

        def build_auto_approve_status(
            self,
            *,
            timings,
            sync_workers,
            session_payload_cache=None,
            capture_sessions=None,
        ):
            assert sync_workers is False
            selected = set(self.sessions if capture_sessions is None else capture_sessions)
            captured.append(selected)
            cached = dict(session_payload_cache or {})
            sessions = {}
            for name in self.sessions:
                if name in selected or name not in cached:
                    CadenceStatusApp.revision += 1
                    sessions[name] = {"session": name, "revision": CadenceStatusApp.revision}
                else:
                    sessions[name] = dict(cached[name])
            return {"session_order": list(self.sessions), "sessions": sessions, "errors": [], "rules": {}}, 200

    monkeypatch.setattr(statusd, "TmuxWebtermApp", CadenceStatusApp)
    service = statusd.PersistentStatusService(
        tmp_path / "statusd.sock",
        wall_clock=lambda: wall[0],
        monotonic=lambda: monotonic[0],
        session_activity_reader=lambda: (dict(activity), None),
        session_jitter=lambda _lower, _upper: 0.0,
    )

    service._build(("cold",))
    assert captured[-1] == {"cold"}
    assert service.session_capture_due_at["cold"] - monotonic[0] == pytest.approx(
        statusd.STATUSD_SESSION_COLD_INTERVAL_SECONDS
    )

    monotonic[0] += statusd.STATUSD_SNAPSHOT_MAX_AGE_SECONDS
    wall[0] += statusd.STATUSD_SNAPSHOT_MAX_AGE_SECONDS
    service._build(("cold",))
    assert captured[-1] == set(), "cold session was captured again on the fast reconciliation tick"

    activity["cold"] = int(wall[0] + 1)
    wall[0] += 1
    monotonic[0] += 1
    service._build(("cold",))
    assert captured[-1] == {"cold"}, "new tmux activity waited for the old cold-session deadline"
    assert service.status()["session_capture_promotions"] == 1
    assert service.session_capture_due_at["cold"] - monotonic[0] == pytest.approx(
        statusd.STATUSD_SNAPSHOT_MAX_AGE_SECONDS
    )


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
        f"cd {REPO_ROOT} && exec python3 tools/mockers/claude.py --mock",
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
        wait_for_direct_snapshot(service, request)
        assert service.snapshot_payload["sessions"][session]["screen"]["key"] == "working"

        # The mock TUI ignores keystrokes while "working" (matching real Claude behavior of not
        # accepting input mid-turn), so switch fixtures by respawning the pane's process rather
        # than typing into a composer that isn't accepting input.
        respawned = tmux_cmd(
            tmux_binary, socket_path, "respawn-pane", "-k", "-t", f"{session}:",
            f"cd {REPO_ROOT} && exec python3 tools/mockers/claude.py --mock",
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
            service.session_capture_due_at[session] = 0.0

        refresh_completed = Event()
        real_build = service._build

        def observed_build(sessions):
            try:
                return real_build(sessions)
            finally:
                refresh_completed.set()

        monkeypatch.setattr(service, "_build", observed_build)
        stale_response, stale_body = service.handle(request)
        assert stale_response["ok"] is True
        assert stale_response["stale"] is True
        assert stale_response["generation"] == metadata.generation
        assert stale_body == body
        assert refresh_completed.wait(timeout=10)
        with service.lock:
            assert service.snapshot[0].generation > metadata.generation
        assert service.snapshot_payload["sessions"][session]["screen"]["key"] == "idle"
    finally:
        if "service" in locals():
            service.stop_event.set()
            with service.lock:
                service.lock.notify_all()
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
        f"cd {REPO_ROOT} && exec python3 tools/mockers/claude.py --mock",
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
            response, body = wait_for_client_snapshot(client, [session])
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
            f"cd {REPO_ROOT} && exec python3 tools/mockers/claude.py --mock",
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
            service.session_capture_due_at[session] = 0.0
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
        def build_auto_approve_status(self, *, timings, sync_workers, **_kwargs):
            assert sync_workers is False
            timings["discover_sessions"] = 0.0
            return {"session_order": list(self.sessions), "sessions": {"1": {"agent_windows": []}}, "errors": [], "rules": {}}, 200

    monkeypatch.setattr(statusd, "TmuxWebtermApp", SessionStatusApp)
    service = statusd.PersistentStatusService(tmp_path / "statusd.sock")
    request = {"action": "snapshot", "protocol_version": statusd.STATUSD_PROTOCOL_VERSION, "sessions": ["1"]}

    metadata, body = wait_for_direct_snapshot(service, request)
    session_metadata, session_body = service.handle({**request, "session": "1"})

    assert json.loads(body)["agent_window_snapshot_revision"] == metadata["generation"] == 1
    assert json.loads(session_body)["agent_window_snapshot_revision"] == session_metadata["generation"] == 1
    service.stop_event.set()
    with service.lock:
        service.lock.notify_all()


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
    service.start_refresh_worker()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(service.handle, [request, request]))

    assert all(result[0].get("ok") is True or result[0].get("error") == "refreshing" for result in results)
    first, first_body = wait_for_direct_snapshot(service, request)
    second, second_body = service.handle(request)

    assert FakeStatusApp.builds == 1
    assert first["generation"] == second["generation"] == 1
    assert first_body == second_body
    service.stop_event.set()
    with service.lock:
        service.lock.notify_all()


def test_statusd_slow_refresh_serves_retained_snapshot_without_waiting(monkeypatch, tmp_path):
    build_started = Event()
    release_build = Event()
    monkeypatch.setattr(control_module, "CONTROL_SOCKET_DIR", tmp_path / "control")

    class BlockingStatusApp(FakeStatusApp):
        builds = 0

        def build_auto_approve_status(self, *, timings, sync_workers, **_kwargs):
            assert sync_workers is False
            BlockingStatusApp.builds += 1
            if BlockingStatusApp.builds == 2:
                build_started.set()
                assert release_build.wait(timeout=5)
            timings["discover_sessions"] = 0.0
            return {"session_order": list(self.sessions), "sessions": {}, "errors": [], "rules": {}}, 200

    monkeypatch.setattr(statusd, "TmuxWebtermApp", BlockingStatusApp)
    socket_path = tmp_path / "services" / "statusd.sock"
    service = statusd.PersistentStatusService(socket_path, idle_seconds=60.0)
    service_thread = Thread(target=service.run, daemon=True)
    service_thread.start()
    web_app = TmuxWebtermApp(["1"])
    web_app.status_client = StatusClient(socket_path)
    monkeypatch.setattr(web_app, "merge_shared_attention_acks", lambda: False)
    monkeypatch.setattr(web_app, "build_auto_approve_status", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("web must not build status")))

    try:
        assert web_app.status_client.ensure_started() is True
        wait_for_client_snapshot(web_app.status_client, ["1"])
        first_body, first_status = web_app.auto_approve_status_bytes()
        first_generation = service.status()["generation"]
        web_app.status_client.invalidate("test")
        rebuilding, rebuilding_body = web_app.status_client.snapshot(["1"], None, 4.0)
        assert rebuilding.get("stale") is True and rebuilding_body == first_body
        assert build_started.wait(timeout=5)
        started = time.perf_counter()
        stale_body, stale_status = web_app.auto_approve_status_bytes()
        elapsed = time.perf_counter() - started
        release_build.set()
        refreshed, _refreshed_body = wait_for_client_snapshot(web_app.status_client, ["1"])
    finally:
        release_build.set()
        web_app.control_server.stop()
        web_app.status_client.request({"action": "shutdown"})
        service_thread.join(timeout=2.0)

    assert first_status == stale_status == HTTPStatus.OK
    assert elapsed < 0.25
    assert stale_body == first_body
    assert refreshed["generation"] > first_generation
    assert service_thread.is_alive() is False


def test_statusd_invalidate_during_build_forces_followup_generation(monkeypatch, tmp_path):
    build_started = Event()
    release_build = Event()
    followup_build_started = Event()

    class InvalidatedBuildStatusApp(FakeStatusApp):
        builds = 0

        def build_auto_approve_status(self, *, timings, sync_workers, **_kwargs):
            assert sync_workers is False
            InvalidatedBuildStatusApp.builds += 1
            if InvalidatedBuildStatusApp.builds == 2:
                build_started.set()
                assert release_build.wait(timeout=5)
            elif InvalidatedBuildStatusApp.builds == 3:
                followup_build_started.set()
            timings["discover_sessions"] = 0.0
            return {"session_order": list(self.sessions), "sessions": {}, "errors": [], "rules": {}}, 200

    monkeypatch.setattr(statusd, "TmuxWebtermApp", InvalidatedBuildStatusApp)
    socket_path = tmp_path / "services" / "statusd.sock"
    service = statusd.PersistentStatusService(socket_path, idle_seconds=60.0)
    service_thread = Thread(target=service.run, daemon=True)
    service_thread.start()
    client = StatusClient(socket_path)
    try:
        assert client.ensure_started() is True
        initial, _initial_body = wait_for_client_snapshot(client, ["one"])
        lease = client.acquire_generation_lease()
        assert lease["ok"] is True
        assert client.invalidate("same-reason")["ok"] is True
        stale, stale_body = client.snapshot(["one"], timeout=0.25)
        assert stale.get("stale") is True and stale_body
        assert build_started.wait(timeout=2)
        assert client.invalidate("same-reason")["ok"] is True
        release_build.set()
        assert followup_build_started.wait(timeout=2)
        first_refresh = client.wait_generation(initial["generation"], 2.0)
        assert first_refresh["changed"] is True
        final, final_body = wait_for_client_snapshot(client, ["one"])
    finally:
        release_build.set()
        if "lease" in locals() and lease.get("lease_id"):
            client.release_generation_lease(lease["lease_id"])
        client.request({"action": "shutdown"})
        service_thread.join(timeout=2.0)

    assert final["generation"] == initial["generation"] + 2 == 3
    assert final.get("stale") is False
    assert final_body
    assert InvalidatedBuildStatusApp.builds == 3
    assert service.status()["invalidation_reason"] == ""
    assert service_thread.is_alive() is False


def test_statusd_divergent_snapshot_build_never_misses_rpc_deadline(monkeypatch, tmp_path):
    """A rebuilding session set cannot make another live RPC miss its deadline.

    The deadline property is unchanged: neither divergent read raises, and both answer with the
    bounded transient `refreshing` outcome. What changed is the ownership assertion. This test
    used to assert ``queue_depth == 0`` after both divergent reads -- that is, statusd counted the
    conflict and then FORGOT the roster, so its 503 (a bounded 202 pending over HTTP) could only
    ever be resolved by a later independent demand for the same roster. It now asserts one
    retained request naming that exact roster, built after ["one"]'s build commits its generation.
    """
    build_started = Event()
    release_build = Event()

    class BlockingStatusApp(FakeStatusApp):
        builds = 0

        def build_auto_approve_status(self, *, timings, sync_workers, **_kwargs):
            assert sync_workers is False
            BlockingStatusApp.builds += 1
            if BlockingStatusApp.builds == 2:
                build_started.set()
                assert release_build.wait(timeout=5)
            timings["discover_sessions"] = 0.0
            return {"session_order": list(self.sessions), "sessions": {}, "errors": [], "rules": {}}, 200

    monkeypatch.setattr(statusd, "TmuxWebtermApp", BlockingStatusApp)
    socket_path = short_tmux_socket_path("yostatusd-rpc")
    service = statusd.PersistentStatusService(socket_path, idle_seconds=60.0)
    service_thread = Thread(target=service.run, daemon=True)
    service_thread.start()
    client = StatusClient(socket_path)

    def request_other_sessions(timeout_seconds):
        envelope = rpc.new_envelope(
            "statusd",
            "snapshot",
            stamped_request("snapshot", sessions=["two"]),
            timeout_seconds=0.1,
        )
        return rpc.request(socket_path, envelope, timeout_seconds=timeout_seconds)

    try:
        assert client.ensure_started() is True
        wait_for_client_snapshot(client, ["one"])
        assert client.invalidate("test")["ok"] is True
        rebuilding, rebuilding_body = client.snapshot(["one"], None, 4.0)
        assert rebuilding.get("stale") is True and rebuilding_body
        assert build_started.wait(timeout=5)
        failures = []
        responses = []
        for timeout_seconds in (0.1, 2.0):
            try:
                responses.append(request_other_sessions(timeout_seconds)[0])
            except (TimeoutError, rpc.LocalRpcError) as error:
                failures.append(f"{type(error).__name__}: {error}")
        # Read the whole conflict-window state before releasing the build: after the release,
        # reconverging ["one"] legitimately raises the conflict count again.
        during_conflict = service.status()
        with service.lock:
            retained_request = service.refresh_requested_sessions
        release_build.set()
        # ["one"] still reconverges; the retained ["two"] build never displaces its demand.
        deadline = time.monotonic() + 5.0
        while True:
            settled, settled_body = client.snapshot(["one"], timeout=0.25)
            if settled.get("ok") is True and settled.get("stale") is not True:
                break
            assert time.monotonic() < deadline, settled
            time.sleep(0.02)
        assert failures == []
        assert responses == [
            {"ok": False, "status": HTTPStatus.SERVICE_UNAVAILABLE, "error": "refreshing"},
            {"ok": False, "status": HTTPStatus.SERVICE_UNAVAILABLE, "error": "refreshing"},
        ]
        assert settled_body
        assert during_conflict["snapshot_build_conflicts"] == 2
        # Both divergent reads named the same roster, so the second superseded nothing.
        assert during_conflict["queue_depth"] == 1
        assert during_conflict["snapshot_refresh_supersessions"] == 0
        assert retained_request == ("two",)
    finally:
        release_build.set()
        client.request({"action": "shutdown"})
        service_thread.join(timeout=2.0)

    assert service_thread.is_alive() is False


DIVERGENT_ROSTER_A = ("one",)
DIVERGENT_ROSTER_B = ("one", "two")


class DivergentRosterBarrierApp(FakeStatusApp):
    """A status app whose builds are individually gated, keyed by the roster being built.

    Independent gates are what make the barrier assertions exact rather than timing-dependent:
    the test can stop the world in the window between roster A committing and roster B starting.
    A roster with no gate entry builds without blocking.
    """

    gates: dict[tuple[str, ...], Event] = {}
    entered: dict[tuple[str, ...], Event] = {}
    built: list[tuple[str, ...]] = []

    def build_auto_approve_status(self, *, timings, sync_workers, **_kwargs):
        assert sync_workers is False
        roster = tuple(self.sessions)
        DivergentRosterBarrierApp.built.append(roster)
        entered = DivergentRosterBarrierApp.entered.get(roster)
        if entered is not None:
            entered.set()
        gate = DivergentRosterBarrierApp.gates.get(roster)
        if gate is not None:
            assert gate.wait(timeout=10)
        timings["discover_sessions"] = 0.0
        return {
            "session_order": list(self.sessions),
            "sessions": {name: {"session": name, "enabled": False} for name in self.sessions},
            "errors": [],
            "rules": {},
        }, 200


def _divergent_roster_barrier_service(monkeypatch, tmp_path, gated=(DIVERGENT_ROSTER_A, DIVERGENT_ROSTER_B)):
    FakeStatusApp.builds = 0
    FakeStatusApp.fail = False
    DivergentRosterBarrierApp.built = []
    DivergentRosterBarrierApp.gates = {roster: Event() for roster in gated}
    DivergentRosterBarrierApp.entered = {}
    monkeypatch.setattr(statusd, "TmuxWebtermApp", DivergentRosterBarrierApp)
    return statusd.PersistentStatusService(tmp_path / "statusd.sock")


def _stop_divergent_roster_barrier(service):
    for gate in DivergentRosterBarrierApp.gates.values():
        gate.set()
    service.stop_event.set()
    with service.lock:
        service.lock.notify_all()


def _entered_event(roster):
    return DivergentRosterBarrierApp.entered.setdefault(roster, Event())


def assert_statusd_owns_a_divergent_roster_demanded_during_a_build(service):
    """Hold roster A's build, demand roster B EXACTLY ONCE, release A, never demand B again.

    Statusd alone must build and then serve B. Every read of B in here happens strictly after B's
    build has already been observed, so no read in this barrier can be the thing that scheduled it.
    """
    _entered_event(DIVERGENT_ROSTER_A)
    _entered_event(DIVERGENT_ROSTER_B)
    service.start_refresh_worker()

    assert service.handle(_statusd_snapshot_request(list(DIVERGENT_ROSTER_A)))[0] == {
        "ok": False, "status": HTTPStatus.SERVICE_UNAVAILABLE, "error": "refreshing",
    }
    assert _entered_event(DIVERGENT_ROSTER_A).wait(timeout=5), "roster A's build never started"

    # The one and only demand for roster B.
    assert service.handle(_statusd_snapshot_request(list(DIVERGENT_ROSTER_B)))[0] == {
        "ok": False, "status": HTTPStatus.SERVICE_UNAVAILABLE, "error": "refreshing",
    }
    assert service.status()["snapshot_build_conflicts"] == 1
    DivergentRosterBarrierApp.gates[DIVERGENT_ROSTER_A].set()

    assert _entered_event(DIVERGENT_ROSTER_B).wait(timeout=3), (
        "statusd never scheduled the divergent roster: the demand stayed pending with no owner "
        f"(built={DivergentRosterBarrierApp.built}, status={service.status()})"
    )

    # B was scheduled only after A committed. A's snapshot and generation are still the retained
    # ones right now, so A's waiters are served rather than displaced by B's schedule.
    with service.lock:
        assert service.snapshot_session_names == DIVERGENT_ROSTER_A
        assert service.snapshot[0].generation == 1
        assert service.refresh_build_sessions == DIVERGENT_ROSTER_B
    served_a, served_a_body = service.handle(_statusd_snapshot_request(list(DIVERGENT_ROSTER_A), session="one"))
    assert served_a["ok"] is True and served_a["generation"] == 1, served_a
    assert json.loads(served_a_body)["session"] == "one"

    DivergentRosterBarrierApp.gates[DIVERGENT_ROSTER_B].set()
    deadline = time.monotonic() + 5.0
    with service.lock:
        while service.snapshot_session_names != DIVERGENT_ROSTER_B:
            remaining = deadline - time.monotonic()
            assert remaining > 0, "roster B built but never committed its snapshot"
            service.lock.wait(remaining)

    served_b, served_b_body = service.handle(_statusd_snapshot_request(list(DIVERGENT_ROSTER_B), session="two"))
    assert served_b["ok"] is True and served_b.get("stale") is False, served_b
    assert served_b["generation"] == 2, served_b
    assert json.loads(served_b_body)["session"] == "two"
    assert DivergentRosterBarrierApp.built == [DIVERGENT_ROSTER_A, DIVERGENT_ROSTER_B]
    assert service.status()["queue_depth"] == 0
    assert service.status()["snapshot_refresh_supersessions"] == 0


def test_statusd_builds_a_divergent_roster_demanded_once_during_another_rosters_build(monkeypatch, tmp_path):
    """Regression: a divergent roster returned a bounded pending that no owner would ever resolve.

    `_snapshot` counted `snapshot_build_conflicts` for a roster demanded mid-build and then dropped
    it. The age-based reconciler in `refresh_loop` rebuilds `session_names` -- the roster that just
    built -- so nothing remembered the divergent one. Measured before the fix: roster B demanded
    once was still unbuilt after 6s with `queue_depth == 0`, and with a live generation lease held
    the reconciler produced three more builds, all of roster A.
    """
    service = _divergent_roster_barrier_service(monkeypatch, tmp_path)
    try:
        assert_statusd_owns_a_divergent_roster_demanded_during_a_build(service)
    finally:
        _stop_divergent_roster_barrier(service)


def test_divergent_roster_barrier_goes_red_without_the_scheduling_owner(monkeypatch, tmp_path):
    """Negative control for the barrier above: remove the owner and the barrier must fail.

    `_retain_refresh_request` is replaced with the exact pre-fix rule -- a roster demanded while a
    DIFFERENT roster builds is dropped -- and the same assertion helper must raise on the "never
    scheduled" barrier. Without this, a barrier that merely re-demanded B would pass either way.
    """
    service = _divergent_roster_barrier_service(monkeypatch, tmp_path)
    retain = service._retain_refresh_request

    def drop_divergent_requests(sessions):
        if service.refresh_build_sessions is not None and service.refresh_build_sessions != sessions:
            return
        retain(sessions)

    monkeypatch.setattr(service, "_retain_refresh_request", drop_divergent_requests)
    try:
        with pytest.raises(AssertionError, match="statusd never scheduled the divergent roster"):
            assert_statusd_owns_a_divergent_roster_demanded_during_a_build(service)
        assert DivergentRosterBarrierApp.built == [DIVERGENT_ROSTER_A]
        assert service.status()["queue_depth"] == 0
    finally:
        _stop_divergent_roster_barrier(service)


def test_statusd_retains_only_the_latest_of_three_divergent_rosters_during_one_build(monkeypatch, tmp_path):
    """The retained request is bounded to one latest roster, and a superseded roster is counted.

    Three divergent rosters arriving during one build leave only the third retained. The first two
    are not silently dropped: each already holds a typed `refreshing` outcome, the supersession is
    recorded in `snapshot_refresh_supersessions`, and one later demand re-registers the roster.
    """
    service = _divergent_roster_barrier_service(monkeypatch, tmp_path, gated=(DIVERGENT_ROSTER_A,))
    try:
        service.start_refresh_worker()
        service.handle(_statusd_snapshot_request(list(DIVERGENT_ROSTER_A)))
        assert _entered_event(DIVERGENT_ROSTER_A).wait(timeout=5), "roster A's build never started"

        for roster in (("b",), ("c",), ("d",)):
            assert service.handle(_statusd_snapshot_request(list(roster)))[0] == {
                "ok": False, "status": HTTPStatus.SERVICE_UNAVAILABLE, "error": "refreshing",
            }
        during_conflict = service.status()
        with service.lock:
            retained_request = service.refresh_requested_sessions
        DivergentRosterBarrierApp.gates[DIVERGENT_ROSTER_A].set()

        deadline = time.monotonic() + 5.0
        with service.lock:
            while service.snapshot_session_names != ("d",):
                remaining = deadline - time.monotonic()
                assert remaining > 0, f"the retained roster never built (built={DivergentRosterBarrierApp.built})"
                service.lock.wait(remaining)
        assert DivergentRosterBarrierApp.built == [DIVERGENT_ROSTER_A, ("d",)]

        # A superseded roster is owed a build again on its next demand, not starved forever.
        deadline = time.monotonic() + 5.0
        while True:
            recovered, recovered_body = service.handle(_statusd_snapshot_request(["b"], session="b"))
            if recovered.get("ok") is True:
                break
            assert time.monotonic() < deadline, recovered
            time.sleep(0.02)
        assert json.loads(recovered_body)["session"] == "b"
    finally:
        _stop_divergent_roster_barrier(service)

    assert retained_request == ("d",)
    assert during_conflict["queue_depth"] == 1
    assert during_conflict["snapshot_build_conflicts"] == 3
    assert during_conflict["snapshot_refresh_supersessions"] == 2


def test_statusd_first_snapshot_schedules_build_without_missing_rpc_deadline(monkeypatch, tmp_path):
    build_started = Event()
    release_build = Event()

    class BlockingFirstStatusApp(FakeStatusApp):
        def build_auto_approve_status(self, *, timings, sync_workers, **_kwargs):
            assert sync_workers is False
            build_started.set()
            assert release_build.wait(timeout=5)
            timings["discover_sessions"] = 0.0
            return {"session_order": list(self.sessions), "sessions": {}, "errors": [], "rules": {}}, 200

    monkeypatch.setattr(statusd, "TmuxWebtermApp", BlockingFirstStatusApp)
    socket_path = tmp_path / "services" / "statusd.sock"
    service = statusd.PersistentStatusService(socket_path, idle_seconds=60.0)
    service_thread = Thread(target=service.run, daemon=True)
    service_thread.start()
    client = StatusClient(socket_path)

    try:
        assert client.ensure_started() is True
        started = time.perf_counter()
        first, first_body = client.snapshot(["one"], timeout=0.1)
        elapsed = time.perf_counter() - started
        assert build_started.wait(timeout=1)
        release_build.set()
        deadline = time.monotonic() + 2.0
        while True:
            completed, completed_body = client.snapshot(["one"], timeout=0.2)
            if completed.get("ok") is True:
                break
            assert time.monotonic() < deadline
            release_build.wait(0.01)
    finally:
        release_build.set()
        client.request({"action": "shutdown"})
        service_thread.join(timeout=2.0)

    assert elapsed < 0.25
    assert first == {"ok": False, "status": HTTPStatus.SERVICE_UNAVAILABLE, "error": "refreshing"}
    assert first_body == b""
    assert completed["generation"] == 1
    assert completed_body
    assert service_thread.is_alive() is False


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
        first_response, first_body = wait_for_client_snapshot(first, ["1"])
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
        initial, _body = wait_for_client_snapshot(client, ["1"])
        lease = client.acquire_generation_lease()
        assert lease["ok"] is True
        with ThreadPoolExecutor(max_workers=1) as executor:
            waiting = executor.submit(client.wait_generation, initial["generation"], 2.0)
            # This snapshot would time out with the former serial listener while
            # the waiter owns the only accepted connection.
            concurrent, concurrent_body = client.snapshot(["1"], timeout=1.0)
            assert concurrent["generation"] == initial["generation"]
            assert concurrent_body
            assert client.invalidate("test")["ok"] is True
            waited = waiting.result(timeout=2.0)
        refreshed, _body = client.snapshot(["1"], timeout=1.0)
        assert refreshed["generation"] > initial["generation"]
        assert waited["changed"] is True
        assert waited["generation"] == refreshed["generation"]
    finally:
        if 'lease' in locals() and lease.get("lease_id"):
            client.release_generation_lease(lease["lease_id"])
        client.request({"action": "shutdown"})
        thread.join(timeout=2.0)

    assert thread.is_alive() is False


def test_statusd_no_change_generation_wait_survives_concurrent_snapshots(monkeypatch, tmp_path):
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
        initial, initial_body = wait_for_client_snapshot(client, ["1"])
        lease = client.acquire_generation_lease()
        assert lease["ok"] is True
        assert client.invalidate("prime-composed-wait")["ok"] is True
        primed = client.wait_generation(initial["generation"], 1.0)
        baseline, baseline_body = client.snapshot(["1"], timeout=1.0)
        assert primed.get("generation") == baseline["generation"]
        with ThreadPoolExecutor(max_workers=2) as executor:
            waiting = executor.submit(client.wait_generation, baseline["generation"], 1.0)
            snapshots = list(executor.map(lambda _index: client.snapshot(["1"], timeout=1.0), range(3)))
            waited = waiting.result(timeout=2.0)
        assert waited == {
            "ok": True,
            "protocol_version": statusd.STATUSD_PROTOCOL_VERSION,
            "changed": False,
            "generation": baseline["generation"],
        }
        assert initial_body
        assert all(response.get("ok") is True and body == baseline_body for response, body in snapshots)
    finally:
        if "lease" in locals() and lease.get("lease_id"):
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
    service.leases["dead-client"] = {"pid": 12345}
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
        assert lease["ok"] is False
        assert lease["diagnostic"]["reason"] == "process_not_found"
        thread.join(timeout=2.5)
        assert thread.is_alive() is False
    finally:
        if thread.is_alive():
            client.request({"action": "shutdown"})
            thread.join(timeout=2.0)


def test_two_web_apps_forward_one_shared_statusd_snapshot_without_local_build(monkeypatch, tmp_path, no_control_socket):
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
        wait_for_client_snapshot(first_app.status_client, ["1"])
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


def test_web_status_byte_forwarder_never_calls_in_process_status_builder(monkeypatch, no_control_socket):
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


def test_web_read_forwards_typed_statusd_unavailable_to_shared_response_parent(monkeypatch, no_control_socket):
    app = TmuxWebtermApp(["1"])
    monkeypatch.setattr(app, "merge_shared_attention_acks", lambda: False)
    monkeypatch.setattr(app.status_client, "snapshot", lambda sessions, session=None, timeout=1.0: ({"ok": False, "status": int(HTTPStatus.SERVICE_UNAVAILABLE), "error": "unavailable"}, b""))
    monkeypatch.setattr(app, "build_auto_approve_status", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("web must not build status when statusd is down")))
    try:
        body, status = app.auto_approve_status_bytes()
    finally:
        app.control_server.stop()

    assert status == HTTPStatus.SERVICE_UNAVAILABLE
    assert json.loads(body) == {
        "ok": False,
        "status": HTTPStatus.SERVICE_UNAVAILABLE,
        "error": "unavailable",
    }


def test_web_read_forwards_statusd_transport_timeout_to_shared_response_parent(monkeypatch, no_control_socket):
    app = TmuxWebtermApp(["1"])
    monkeypatch.setattr(app, "merge_shared_attention_acks", lambda: False)
    monkeypatch.setattr(
        app.status_client,
        "snapshot",
        lambda sessions, session=None, timeout=1.0: (
            {"ok": False, "_transport_error": "timeout", "error": "timed out"},
            b"",
        ),
    )
    try:
        body, status = app.auto_approve_status_bytes()
    finally:
        app.control_server.stop()

    assert status == HTTPStatus.SERVICE_UNAVAILABLE
    assert json.loads(body) == {
        "ok": False,
        "_transport_error": "timeout",
        "error": "timed out",
    }


def test_web_read_forwards_stale_bytes_when_statusd_build_fails_after_invalidation(monkeypatch, tmp_path, no_control_socket):
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
        wait_for_client_snapshot(web_app.status_client, ["1"])
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
