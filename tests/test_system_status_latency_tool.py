# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

from pathlib import Path
import subprocess
import sys

from tools import system_status_latency_probe as probe
from tests.isolated_dev_server import build_paths
from tests.isolated_dev_server import start_isolated_dev_server
from tests.tmux_runtime import start_isolated_tmux_runtime
from tests.tmux_runtime import stop_isolated_tmux_runtime


def test_probe_fails_zero_or_multiple_listeners(monkeypatch, tmp_path):
    for pids in ([], [1, 2]):
        monkeypatch.setattr(probe, "listener_pids", lambda _port, pids=pids: pids)
        assert probe.main(["--port", "49152", "--scheme", "http", "--output", str(tmp_path / "out.json")]) == 2


def test_probe_acceptance_failure_is_nonzero(monkeypatch, tmp_path):
    output = tmp_path / "out.json"
    monkeypatch.setattr(probe, "listener_pids", lambda _port: [123])
    monkeypatch.setattr(probe, "process_identity", lambda _pid: {"pid": 123, "start_ticks": 1, "cwd": "/repo", "sha": "a" * 40})
    monkeypatch.setattr(probe, "process_environment", lambda _pid: {"YOLOMUX_CONFIG_DIR": str(tmp_path)})
    monkeypatch.setattr(probe, "config_dir_from_process", lambda _env: tmp_path)
    monkeypatch.setattr(probe, "auth_cookie", lambda _root, _port: "cookie")
    values = [5.0] * 197 + [25.0, 25.5, 26.0]
    markers = iter(f"capture-{index:032x}" for index in range(probe.SAMPLES))
    monkeypatch.setattr(probe.secrets, "token_hex", lambda _count: next(markers).removeprefix("capture-"))
    records = [{"details": {"measurement_request_id": probe.hashlib.sha256(f"capture-{index:032x}".encode()).hexdigest()[:16], probe.METRIC: value}} for index, value in enumerate(values)]
    calls = {"count": 0}
    def fake_get(url, cookie, marker=""):
        calls["count"] += 1
        if "diagnostics/performance" in url:
            return 200, {"perf": {"recent": records}}
        return 200, {"ok": True}
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
    proc = tmp_path / "123"
    proc.mkdir()
    (proc / "stat").write_text("123 (probe) " + " ".join(["S"] + ["0"] * 18 + ["99"] + ["0"] * 5))
    (proc / "cwd").symlink_to(tmp_path)
    monkeypatch.setattr(probe, "Path", lambda value: Path(str(value).replace("/proc/123", str(proc))))
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
        deadline = probe.time.monotonic() + 10
        while True:
            status, body = probe.get_json(server.base_url + "/api/system-status", cookie)
            if status == 200 and isinstance(body, dict) and body.get("ok") is True:
                break
            if probe.time.monotonic() >= deadline:
                raise AssertionError(f"ephemeral server snapshot did not publish: {status} {body}")
        output = tmp_path / "latency.json"
        completed = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve().parents[1] / "tools" / "system_status_latency_probe.py"),
                "--port",
                str(server.port),
                "--scheme",
                "http",
                "--output",
                str(output),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        # The host decides whether the fixed 20 ms product budget passes; either acceptance result
        # must still be a valid completed run. Probe/setup errors are exit 2 and are never accepted.
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
