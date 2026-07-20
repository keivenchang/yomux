import argparse
import json
import socket
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from yolomux_lib import cli
from yolomux_lib.local_services import registry as cli_registry
from yolomux_lib.server import TmuxWebtermHTTPServer
from yolomux_lib.server import https_redirect_response


def cli_args(**overrides):
    values = {
        "self_signed": False,
        "http": False,
        "cert": None,
        "key": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def fake_ssl_context(monkeypatch):
    loaded = {}

    class FakeContext:
        minimum_version = None

        def load_cert_chain(self, certfile, keyfile):
            loaded.update(certfile=certfile, keyfile=keyfile)

    context = FakeContext()
    monkeypatch.setattr(cli.ssl, "SSLContext", lambda _protocol: context)
    return context, loaded


def test_tls_default_is_self_signed_https(monkeypatch, tmp_path):
    cert_path = tmp_path / "self-signed.crt"
    key_path = tmp_path / "self-signed.key"
    monkeypatch.setattr(cli, "ensure_self_signed_cert", lambda: (cert_path, key_path))
    expected_context, loaded = fake_ssl_context(monkeypatch)

    context, message = cli.tls_context_for_args(cli_args())

    assert context is expected_context
    assert loaded == {"certfile": str(cert_path), "keyfile": str(key_path)}
    assert "self-signed HTTPS certificate" in message
    assert "SAN:" in message
    assert "tools/setup-tls.sh" in message


def test_self_signed_san_includes_localhost_hostname_and_interface_ips(monkeypatch):
    monkeypatch.setattr(cli, "SERVER_HOSTNAME", "yolomux-host")
    monkeypatch.setattr(cli, "self_signed_interface_ips", lambda: ("10.110.42.35", "192.168.50.9"))

    assert cli.self_signed_san().split(",") == [
        "DNS:localhost",
        "IP:127.0.0.1",
        "DNS:yolomux-host",
        "IP:10.110.42.35",
        "IP:192.168.50.9",
    ]


def test_setup_tls_dry_run_uses_shared_san_and_state_dir(tmp_path):
    if not cli.shutil.which("openssl"):
        pytest.skip("openssl is required for the TLS setup contract")
    state_dir = tmp_path / "state"

    result = subprocess.run(
        [
            "bash",
            "tools/setup-tls.sh",
            "--dry-run",
            "--no-trust",
            "--san",
            "192.168.50.10",
        ],
        check=True,
        capture_output=True,
        text=True,
        env={**cli.os.environ, "YOLOMUX_STATE_DIR": str(state_dir)},
    )

    assert "DNS:localhost" in result.stdout
    assert "IP:127.0.0.1" in result.stdout
    assert "IP:192.168.50.10" in result.stdout
    assert f"leaf ->    {state_dir}/tls/self-signed.{{crt,key}}" in result.stdout


def test_tls_requires_cert_and_key_together():
    with pytest.raises(ValueError, match="--cert and --key"):
        cli.tls_cert_key_paths(cli_args(cert=Path("/tmp/cert.pem")))


def test_tls_explicit_cert_overrides_redundant_self_signed_flag(monkeypatch):
    expected_context, loaded = fake_ssl_context(monkeypatch)

    context, message = cli.tls_context_for_args(
        cli_args(self_signed=True, cert=Path("/tmp/cert.pem"), key=Path("/tmp/key.pem"))
    )

    assert context is expected_context
    assert loaded == {"certfile": "/tmp/cert.pem", "keyfile": "/tmp/key.pem"}
    assert message == "Using HTTPS certificate /tmp/cert.pem"


@pytest.mark.parametrize("self_signed", [False, True])
def test_tls_http_opt_out_is_plain_http(self_signed):
    context, message = cli.tls_context_for_args(cli_args(http=True, self_signed=self_signed))

    assert context is None
    assert message == "WARNING: TLS disabled by --http; serving plain HTTP."


def test_tls_rejects_http_with_explicit_cert():
    with pytest.raises(ValueError, match="--http cannot be combined with --cert/--key"):
        cli.tls_cert_key_paths(cli_args(http=True, cert=Path("/tmp/cert.pem"), key=Path("/tmp/key.pem")))


def test_tls_self_signed_alias_remains_accepted(monkeypatch, tmp_path):
    cert_path = tmp_path / "self-signed.crt"
    key_path = tmp_path / "self-signed.key"
    monkeypatch.setattr(cli, "ensure_self_signed_cert", lambda: (cert_path, key_path))
    expected_context, _loaded = fake_ssl_context(monkeypatch)

    context, message = cli.tls_context_for_args(cli_args(self_signed=True))

    assert context is expected_context
    assert "self-signed HTTPS certificate" in message


def test_tls_missing_openssl_falls_back_to_http_with_actionable_warning(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "STATE_DIR", tmp_path)
    monkeypatch.setattr(cli.shutil, "which", lambda _name: None)

    context, message = cli.tls_context_for_args(cli_args())

    assert context is None
    assert "openssl not found" in message
    assert "starting plain HTTP" in message
    assert "pass --cert/--key, or pass --http explicitly" in message


def test_parse_args_supports_sessions_dangerous_yolo_and_self_signed(monkeypatch):
    monkeypatch.setattr(
        cli.sys,
        "argv",
        ["yolomux.py", "--host", "0.0.0.0", "--port", "19001", "--sessions", "1,2", "ant", "--dang", "--self-signed"],
    )

    args = cli.parse_args()

    assert args.host == "0.0.0.0"
    assert args.port == 19001
    assert args.sessions == ["1,2", "ant"]
    assert args.dangerously_yolo is True
    assert args.self_signed is True
    assert args.http is False
    assert args.print_background_owner is False
    assert args.print_runtime_report is False


def test_parse_args_supports_explicit_http_opt_out(monkeypatch):
    monkeypatch.setattr(cli.sys, "argv", ["yolomux.py", "--http"])

    args = cli.parse_args()

    assert args.http is True
    assert args.self_signed is False


def test_print_background_owner_status_outputs_json(monkeypatch, capsys):
    monkeypatch.setattr(cli, "read_background_owner_debug_status", lambda: {"current_owner": {"port": 8003}, "generations": []})

    assert cli.print_background_owner_status() == 0

    assert json.loads(capsys.readouterr().out) == {"current_owner": {"port": 8003}, "generations": []}


def test_parse_args_supports_runtime_report(monkeypatch):
    monkeypatch.setattr(cli.sys, "argv", ["yolomux.py", "--print-runtime-report", "--sessions", "8002"])

    args = cli.parse_args()

    assert args.print_runtime_report is True
    assert args.sessions == ["8002"]


def _forbid_app_construction(monkeypatch):
    def explode(*_args, **_kwargs):
        raise AssertionError("print_runtime_report must never construct a TmuxWebtermApp")

    monkeypatch.setattr(cli, "TmuxWebtermApp", explode)


def test_print_runtime_report_uses_live_owner_control_socket_without_an_app(monkeypatch, capsys):
    _forbid_app_construction(monkeypatch)
    requests = []
    monkeypatch.setattr(cli, "read_background_owner_debug_status", lambda: {"current_owner": {"port": 8002}})

    def fake_control(owner, request):
        requests.append((owner, request))
        return {"ok": True, "report": {"ok": True, "top_endpoints": [{"surface": "GET /api/session-files"}]}}

    monkeypatch.setattr(cli, "send_yolomux_control_request", fake_control)

    assert cli.print_runtime_report(["8002"], dangerously_yolo=True) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["top_endpoints"] == [{"surface": "GET /api/session-files"}]
    assert payload["owner_debug"] == {"current_owner": {"port": 8002}}
    assert requests == [({"port": 8002}, {"action": "runtime_report"})]


def test_print_runtime_report_degrades_to_bounded_ledger_records(monkeypatch, capsys, tmp_path):
    _forbid_app_construction(monkeypatch)
    monkeypatch.setattr(cli, "read_background_owner_debug_status", lambda: {"current_owner": None})
    monkeypatch.setattr(cli, "send_yolomux_control_request", lambda owner, request: {"ok": False, "error": "no owner"})
    monkeypatch.setattr(cli, "STATE_DIR", tmp_path)
    lease_dir = tmp_path / "server-leases"
    lease_dir.mkdir(parents=True)
    (lease_dir / "8881.lock").write_text(json.dumps({"pid": 400, "port": 8881}), encoding="utf-8")
    monkeypatch.setattr(
        cli,
        "bounded_process_table",
        lambda: {400: cli_registry.ProcessTableEntry(1, 400, 5.0, "python3 -u yolomux.py 8880 /tmp/log --port 8881 --dang")},
    )

    assert cli.print_runtime_report([], dangerously_yolo=False) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "bounded-records"
    assert payload["port_groups"] == [{"port": 8881, "pid": 400, "pgid": 400, "member_pids": [400]}]
    assert payload["local_service_groups"] == []


def test_main_maps_cli_flags_to_app_and_server(monkeypatch, capsys):
    captured = {}
    monkeypatch.setenv("YOLOMUX_BACKGROUND_OWNER_PRIMARY_PORT", "19001")
    args = argparse.Namespace(
        host="0.0.0.0",
        port=19001,
        sessions=["1,2", "ant"],
        dangerously_yolo=True,
        self_signed=False,
        http=False,
        cert=None,
        key=None,
        print_transcripts=False,
        print_background_owner=False,
        print_runtime_report=False,
        dev=False,
    )

    class FakeApp:
        def __init__(self, sessions, dangerously_yolo=False):
            captured["sessions"] = sessions
            captured["dangerously_yolo"] = dangerously_yolo

        def restore_auto_approve(self):
            return []

        def start_background_owner(self, port=None, priority=0):
            captured["background_owner_port"] = port
            captured["background_owner_priority"] = priority
            return True

        def start_yoagent_backend_prewarm(self, **kwargs):
            captured["yoagent_prewarm"] = kwargs
            return {"ok": True}, 202

        def stop_auto_approve_all(self):
            captured["stopped"] = True

    class FakeServer:
        def __init__(self, address, app, tls_context=None, dev=False):
            captured["address"] = address
            captured["app"] = app
            captured["tls_context"] = tls_context
            captured["dev"] = dev

        def serve_forever(self):
            captured["served"] = True

        def server_close(self):
            captured["closed"] = True

    monkeypatch.setattr(cli, "parse_args", lambda: args)
    tls_marker = object()
    monkeypatch.setattr(cli, "tls_context_for_args", lambda _args: (tls_marker, "Using self-signed HTTPS certificate /tmp/cert.pem"))
    monkeypatch.setattr(cli, "TmuxWebtermApp", FakeApp)
    monkeypatch.setattr(cli, "TmuxWebtermHTTPServer", FakeServer)
    monkeypatch.setattr(cli, "auth_setup_required", lambda: False)

    assert cli.main() == 0

    output = capsys.readouterr().out
    assert captured["sessions"] == ["1", "2", "ant"]
    assert captured["dangerously_yolo"] is True
    assert captured["address"] == ("0.0.0.0", 19001)
    assert captured["tls_context"] is tls_marker
    assert captured["dev"] is False  # dev mode off by default
    assert captured["background_owner_port"] == 19001
    assert captured["background_owner_priority"] == 100
    assert captured["yoagent_prewarm"] == {"reason": "server_start"}
    assert captured["served"] is True
    assert captured["stopped"] is True
    assert captured["closed"] is True
    assert "DANGEROUS YOLO mode is enabled" in output
    assert "Serving YOLOmux on https://localhost:19001/" in output
    assert "Using self-signed HTTPS certificate /tmp/cert.pem" in output
    assert "Highly recommend" not in output


def test_main_rejects_duplicate_port_before_constructing_the_app(monkeypatch, capsys):
    args = argparse.Namespace(
        host="0.0.0.0",
        port=19002,
        sessions=None,
        dangerously_yolo=False,
        self_signed=False,
        http=False,
        cert=None,
        key=None,
        print_transcripts=False,
        print_background_owner=False,
        print_runtime_report=False,
        dev=False,
    )
    monkeypatch.setattr(cli, "parse_args", lambda: args)
    monkeypatch.setattr(cli, "tls_context_for_args", lambda _args: (None, ""))
    monkeypatch.setattr(cli, "acquire_server_port_lease", lambda _port: None)
    monkeypatch.setattr(cli, "TmuxWebtermApp", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("duplicate launch must not initialize app state")))

    assert cli.main() == 1
    assert "refusing a duplicate" in capsys.readouterr().err


def test_self_signed_cert_generation_is_persistent(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "STATE_DIR", tmp_path)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/openssl")
    monkeypatch.setattr(cli, "self_signed_interface_ips", lambda: ("10.110.42.35",))
    calls = []

    def fake_run(command, check, stdout, stderr, text):
        calls.append(command)
        Path(command[command.index("-out") + 1]).write_text("cert", encoding="utf-8")
        Path(command[command.index("-keyout") + 1]).write_text("key", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    cert_path, key_path = cli.ensure_self_signed_cert()
    second_cert_path, second_key_path = cli.ensure_self_signed_cert()

    assert cert_path == second_cert_path
    assert key_path == second_key_path
    assert cert_path.read_text(encoding="utf-8") == "cert"
    assert key_path.read_text(encoding="utf-8") == "key"
    assert len(calls) == 1
    assert any(item.startswith("subjectAltName=DNS:localhost,IP:127.0.0.1") for item in calls[0])


def test_plain_http_on_tls_port_gets_redirect_response():
    response = https_redirect_response(
        b"GET /foo?bar=1 HTTP/1.1\r\nHost: localhost:19001\r\n\r\n",
        "fallback:19001",
    )

    assert b"308 Permanent Redirect" in response
    assert b"Location: https://localhost:19001/foo?bar=1" in response
    assert b"Use HTTPS" in response


def test_tls_socket_peek_waits_for_delayed_plaintext_first_byte():
    server_socket, client_socket = socket.socketpair()

    try:
        fake_server = SimpleNamespace(tls_context=object(), tls_peek_timeout_seconds=0.4)

        def delayed_client_write():
            time.sleep(0.15)
            client_socket.sendall(b"G")

        writer = threading.Thread(target=delayed_client_write)
        writer.start()
        prepared = TmuxWebtermHTTPServer.prepare_request_socket(fake_server, server_socket)
        writer.join(timeout=1)

        assert prepared is server_socket
        assert server_socket.recv(1) == b"G"
    finally:
        server_socket.close()
        client_socket.close()


def test_tls_socket_peek_does_not_wrap_an_idle_plaintext_preconnect():
    server_socket, client_socket = socket.socketpair()

    class FakeTlsContext:
        def wrap_socket(self, *_args, **_kwargs):
            raise AssertionError("idle plaintext preconnect must not be TLS-wrapped")

    try:
        fake_server = SimpleNamespace(tls_context=FakeTlsContext(), tls_peek_timeout_seconds=0.01)

        prepared = TmuxWebtermHTTPServer.prepare_request_socket(fake_server, server_socket)

        assert prepared is server_socket
        assert server_socket.gettimeout() is None
    finally:
        server_socket.close()
        client_socket.close()


def test_tls_socket_peek_does_not_wrap_tls_like_bytes_with_http_redirect_sentinel():
    server_socket, client_socket = socket.socketpair()

    class FakeTlsContext:
        def wrap_socket(self, *_args, **_kwargs):
            raise AssertionError("plain HTTP share redirect sentinel must not wrap a connection")

    try:
        client_socket.sendall(b"\x16")
        fake_server = SimpleNamespace(tls_context=FakeTlsContext(), tls_peek_timeout_seconds=0.01)

        prepared = TmuxWebtermHTTPServer.prepare_request_socket(fake_server, server_socket)

        assert prepared is server_socket
        assert server_socket.recv(1) == b"\x16"
    finally:
        server_socket.close()
        client_socket.close()
