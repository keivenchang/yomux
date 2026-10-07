# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

from pathlib import Path
import errno
import subprocess
import sys
from types import SimpleNamespace

import pytest

from yomux_lib.infra.listener_census import ListenerCensus
from yomux_lib.infra.listener_census import ListenerDegradation
from yomux_lib.infra.listener_census import SCOPE_GLOBAL
from yomux_lib.infra.listener_census import SCOPE_TARGET

from tools import system_status_latency_probe as probe
from tests.isolated_dev_server import build_paths
from tests.isolated_dev_server import start_isolated_dev_server
from tests.tmux_runtime import start_isolated_tmux_runtime
from tests.tmux_runtime import stop_isolated_tmux_runtime


def test_probe_fails_zero_or_multiple_listeners(monkeypatch, tmp_path):
    for pids in ([], [1, 2]):
        monkeypatch.setattr(probe, "canonicalized_listener_census", lambda _port, pids=pids: ListenerCensus(pids=tuple(pids)))
        assert probe.main(["--port", "49152", "--scheme", "http", "--output", str(tmp_path / "out.json")]) == 2


def test_latency_probe_alone_canonicalizes_same_command_ancestry(monkeypatch):
    parents = {101: 1, 202: 101, 303: 1}
    commands = {101: "python yolomux.py", 202: "python yolomux.py", 303: "foreign"}
    monkeypatch.setattr(probe, "process_parent_pid", parents.get)
    monkeypatch.setattr(probe, "process_command", commands.get)

    monkeypatch.setattr(probe, "listener_census", lambda _port: ListenerCensus(pids=(101, 202)))
    assert probe.canonicalized_listener_census(49152).pids == (101,)
    monkeypatch.setattr(probe, "listener_census", lambda _port: ListenerCensus(pids=(101, 303)))
    assert probe.canonicalized_listener_census(49152).pids == (101, 303)


@pytest.mark.parametrize(
    ("snapshot", "pids", "match"),
    (
        (None, [123], "owner process exited"),
        (SimpleNamespace(state="Z", start_identity="proc:1"), [123], "owner process exited"),
        (SimpleNamespace(state="S", start_identity="proc:2"), [123], "process identity changed"),
        (SimpleNamespace(state="S", start_identity="proc:1"), [], "listener absent while original process remains alive"),
        (SimpleNamespace(state="S", start_identity="proc:1"), [456], "listener takeover.*123 -> 456"),
        (SimpleNamespace(state="S", start_identity="proc:1"), [123, 456], "multiple listener owners"),
    ),
)
def test_final_listener_owner_classifies_each_failure(monkeypatch, snapshot, pids, match):
    monkeypatch.setattr(probe, "process_identity_snapshot", lambda _pid: snapshot)
    monkeypatch.setattr(probe, "canonicalized_listener_census", lambda _port: ListenerCensus(pids=tuple(pids)))

    with pytest.raises(RuntimeError, match=match):
        probe.validate_final_listener_owner(
            49152,
            123,
            {"pid": 123, "start_identity": "proc:1", "cwd": "/repo", "sha": "a" * 40},
        )


def test_final_listener_owner_resamples_identity_after_final_census(monkeypatch):
    snapshots = iter((SimpleNamespace(state="S", start_identity="proc:1"), None))
    monkeypatch.setattr(probe, "process_identity_snapshot", lambda _pid: next(snapshots))
    monkeypatch.setattr(probe, "canonicalized_listener_census", lambda _port: ListenerCensus(pids=(123,)))

    with pytest.raises(RuntimeError, match="became unobservable after final listener census"):
        probe.validate_final_listener_owner(
            49152,
            123,
            {"pid": 123, "start_identity": "proc:1", "cwd": "/repo", "sha": "a" * 40},
        )


def test_final_listener_owner_accepts_global_visibility_degradation(monkeypatch):
    monkeypatch.setattr(
        probe,
        "process_identity_snapshot",
        lambda _pid: SimpleNamespace(state="S", start_identity="proc:1"),
    )
    monkeypatch.setattr(
        probe,
        "canonicalized_listener_census",
        lambda _port: ListenerCensus(
            pids=(123,),
            degradations=(
                ListenerDegradation(
                    pid=456,
                    stage="fd directory",
                    errno_value=errno.EACCES,
                    detail="permission denied",
                    uid=1000,
                    scope=SCOPE_GLOBAL,
                ),
            ),
        ),
    )

    probe.validate_final_listener_owner(
        49152,
        123,
        {"pid": 123, "start_identity": "proc:1", "cwd": "/repo", "sha": "a" * 40},
    )


def test_final_listener_owner_refuses_target_degradation(monkeypatch):
    monkeypatch.setattr(
        probe,
        "process_identity_snapshot",
        lambda _pid: SimpleNamespace(state="S", start_identity="proc:1"),
    )
    monkeypatch.setattr(
        probe,
        "canonicalized_listener_census",
        lambda _port: ListenerCensus(
            pids=(123,),
            degradations=(
                ListenerDegradation(
                    pid=None,
                    stage="unattributed listening inode 4242",
                    errno_value=None,
                    detail="no visible process holds the target inode",
                    scope=SCOPE_TARGET,
                ),
            ),
        ),
    )

    with pytest.raises(RuntimeError, match="cannot prove target ownership"):
        probe.validate_final_listener_owner(
            49152,
            123,
            {"pid": 123, "start_identity": "proc:1", "cwd": "/repo", "sha": "a" * 40},
        )


def test_final_listener_owner_translates_degradation_raised_by_scan(monkeypatch):
    monkeypatch.setattr(
        probe,
        "process_identity_snapshot",
        lambda _pid: SimpleNamespace(state="S", start_identity="proc:1"),
    )
    census = ListenerCensus(
        degradations=(
            ListenerDegradation(
                pid=None,
                stage="unattributed listening inode 4242",
                errno_value=None,
                detail="no visible process holds the target inode",
                scope=SCOPE_TARGET,
            ),
        ),
    )
    monkeypatch.setattr(
        probe,
        "canonicalized_listener_census",
        lambda _port: (_ for _ in ()).throw(probe.ListenerCensusDegraded(49152, census)),
    )

    with pytest.raises(RuntimeError, match="final listener census cannot prove target ownership"):
        probe.validate_final_listener_owner(
            49152,
            123,
            {"pid": 123, "start_identity": "proc:1", "cwd": "/repo", "sha": "a" * 40},
        )


@pytest.mark.parametrize(
    "scan_error",
    (
        probe.ListenerCensusError("fatal proc scan"),
        probe.ListenerCensusTimeout("listener scan timed out"),
    ),
)
def test_final_listener_owner_preserves_fatal_scan_or_timeout(monkeypatch, scan_error):
    monkeypatch.setattr(
        probe,
        "process_identity_snapshot",
        lambda _pid: SimpleNamespace(state="S", start_identity="proc:1"),
    )
    monkeypatch.setattr(
        probe,
        "canonicalized_listener_census",
        lambda _port: (_ for _ in ()).throw(scan_error),
    )

    with pytest.raises(RuntimeError, match=f"final listener census failed: {scan_error}") as raised:
        probe.validate_final_listener_owner(
            49152,
            123,
            {"pid": 123, "start_identity": "proc:1", "cwd": "/repo", "sha": "a" * 40},
        )

    assert raised.value.__cause__ is scan_error


@pytest.mark.parametrize(
    ("response_pids", "response_starts", "record_pid", "match"),
    (
        ((456, 123), (10.0, 10.0), 123, "warmup 0 response came from pid 456"),
        ((123, 456), (10.0, 10.0), 123, "sample 0 response came from pid 456"),
        ((123, 123), (10.0, 11.0), 123, "sample 0 response server identity changed"),
        ((123, 123), (10.0, 10.0), 456, "server record .* came from pid 456"),
    ),
)
def test_probe_refuses_responses_or_records_from_another_server_lifetime(
    monkeypatch,
    tmp_path,
    response_pids,
    response_starts,
    record_pid,
    match,
):
    output = tmp_path / "out.json"
    monkeypatch.setattr(probe, "WARMUPS", 1)
    monkeypatch.setattr(probe, "SAMPLES", 1)
    monkeypatch.setattr(probe, "canonicalized_listener_census", lambda _port: ListenerCensus(pids=(123,)))
    monkeypatch.setattr(probe, "process_identity_snapshot", lambda _pid: SimpleNamespace(state="S", start_identity="proc:1"))
    monkeypatch.setattr(probe, "process_identity", lambda _pid: {"pid": 123, "start_identity": "proc:1", "cwd": "/repo", "sha": "a" * 40})
    monkeypatch.setattr(probe, "process_environment", lambda _pid: {"YOLOMUX_CONFIG_DIR": str(tmp_path)})
    monkeypatch.setattr(probe, "config_dir_from_process", lambda _env, _port: tmp_path)
    monkeypatch.setattr(probe, "auth_cookie", lambda _root, _port, _scheme: "cookie")
    monkeypatch.setattr(probe.secrets, "token_hex", lambda _count: "0" * 32)
    request_id = probe.hashlib.sha256(("capture-" + "0" * 32).encode()).hexdigest()[:16]
    responses = iter(
        (
            (200, {"state": "ready", "server": {"pid": response_pids[0], "started_at": response_starts[0]}}),
            (200, {"state": "ready", "server": {"pid": response_pids[1], "started_at": response_starts[1]}}),
            (200, {"perf": {"recent": [{"details": {"measurement_request_id": request_id, probe.METRIC: 5.0, "process_pid": record_pid}}]}}),
        )
    )
    monkeypatch.setattr(probe, "get_json", lambda *_args, **_kwargs: next(responses))

    with pytest.raises(RuntimeError, match=match):
        probe.run(49152, "http", output)


def test_probe_acceptance_failure_is_nonzero(monkeypatch, tmp_path):
    output = tmp_path / "out.json"
    monkeypatch.setattr(probe, "canonicalized_listener_census", lambda _port: ListenerCensus(pids=(123,)))
    monkeypatch.setattr(probe, "process_identity_snapshot", lambda _pid: SimpleNamespace(state="S", start_identity="proc:1"))
    monkeypatch.setattr(probe, "process_identity", lambda _pid: {"pid": 123, "start_identity": "proc:1", "cwd": "/repo", "sha": "a" * 40})
    monkeypatch.setattr(probe, "process_environment", lambda _pid: {"YOLOMUX_CONFIG_DIR": str(tmp_path)})
    monkeypatch.setattr(probe, "config_dir_from_process", lambda _env, _port: tmp_path)
    monkeypatch.setattr(probe, "auth_cookie", lambda _root, _port, _scheme: "cookie")
    values = [5.0] * 197 + [25.0, 25.5, 26.0]
    markers = iter(f"capture-{index:032x}" for index in range(probe.SAMPLES))
    monkeypatch.setattr(probe.secrets, "token_hex", lambda _count: next(markers).removeprefix("capture-"))
    records = [{"details": {"measurement_request_id": probe.hashlib.sha256(f"capture-{index:032x}".encode()).hexdigest()[:16], probe.METRIC: value, "process_pid": 123}} for index, value in enumerate(values)]
    calls = {"count": 0}
    def fake_get(url, cookie, marker=""):
        calls["count"] += 1
        if "diagnostics/performance" in url:
            return 200, {"perf": {"recent": records}}
        return 200, {"state": "ready", "server": {"pid": 123, "started_at": 10.0}}
    monkeypatch.setattr(probe, "get_json", fake_get)
    assert probe.main(["--port", "49152", "--scheme", "http", "--output", str(output)]) == 1
    assert probe.json.loads(output.read_text())["acceptance_outcome"] == {"ok": False, "p99_ms": 25.0, "budget_ms": 20.0}


def test_config_root_rejects_auth_root_escape(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        probe.config_dir_from_process({"YOLOMUX_ROOT": str(root), "YOLOMUX_CONFIG_DIR": str(outside)})
    except RuntimeError as error:
        assert "escapes" in str(error)
    else:
        raise AssertionError("auth/root mismatch accepted")


def test_process_identity_rejects_missing_sha(monkeypatch, tmp_path):
    monkeypatch.setattr(probe, "process_start_identity", lambda _pid: "proc:99")
    monkeypatch.setattr(probe, "process_cwd", lambda _pid: str(tmp_path))
    monkeypatch.setattr(probe.subprocess, "run", lambda *args, **kwargs: probe.subprocess.CompletedProcess(args[0], 1, "", "not git"))
    try:
        probe.process_identity(123)
    except RuntimeError as error:
        assert "incomplete" in str(error) and "sha" in str(error)
    else:
        raise AssertionError("empty SHA accepted as identity")


def test_standalone_probe_drives_an_ephemeral_authenticated_daemon(monkeypatch, tmp_path):
    root = tmp_path / "standalone"
    paths = build_paths(root)
    paths.auth_config_path.write_text(
        'users:\n  - username: "probe-admin"\n    password: "fixture-password"\n    role: "admin"\n',
        encoding="utf-8",
    )
    tmux_runtime = start_isolated_tmux_runtime(monkeypatch, root, session_count=1)
    server = None
    try:
        server = start_isolated_dev_server(
            "standalone-latency-probe",
            Path(__file__).resolve().parents[1],
            paths,
            tmux_runtime,
            auth_bypass=False,
        )
        cookie = probe.auth_cookie(paths.config_dir, server.port)
        status, body = probe.get_json(server.base_url + "/api/system-status", cookie)
        assert status == 200 and isinstance(body, dict) and body.get("state") == "ready", (status, body)
        output = tmp_path / "latency.json"
        arguments = [sys.executable, str(Path(__file__).resolve().parents[1] / "tools" / "system_status_latency_probe.py"), "--port", str(server.port), "--scheme", "http", "--output", str(output)]
        completed = subprocess.run(
            arguments,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        # The host decides whether the fixed 20 ms product budget passes; either acceptance result
        # must still be a valid completed run. Probe/setup errors are exit 2 and are never accepted.
        if completed.returncode == 2 and "listener identity is incomplete" in completed.stderr:
            pytest.skip("repository identity is unavailable in the isolated test checkout")
        assert completed.returncode in {0, 1}, (completed.stdout, completed.stderr, server.output[-20:])
        artifact = probe.json.loads(output.read_text(encoding="utf-8"))
        assert artifact["identity"]["pid"] == server.process.pid
        assert artifact["identity"]["cwd"] == str(Path(__file__).resolve().parents[1])
        assert len(artifact["raw"][probe.METRIC]) == probe.SAMPLES
        assert len(artifact["raw"]["client_round_trip_ms"]) == probe.SAMPLES
        assert output.with_suffix(".json.sha256").is_file()
    finally:
        if server is not None:
            server.stop()
        stop_isolated_tmux_runtime(tmux_runtime)
