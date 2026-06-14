import argparse
import socket
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from yolomux_lib import cli
from yolomux_lib.server import TmuxWebtermHTTPServer
from yolomux_lib.server import https_redirect_response


def cli_args(**overrides):
    values = {
        "self_signed": False,
        "cert": None,
        "key": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_tls_default_is_plain_http():
    context, message = cli.tls_context_for_args(cli_args())

    assert context is None
    assert message == ""


def test_tls_requires_cert_and_key_together():
    with pytest.raises(ValueError, match="--cert and --key"):
        cli.tls_cert_key_paths(cli_args(cert=Path("/tmp/cert.pem")))


def test_tls_rejects_self_signed_with_explicit_cert():
    with pytest.raises(ValueError, match="cannot be combined"):
        cli.tls_cert_key_paths(cli_args(self_signed=True, cert=Path("/tmp/cert.pem"), key=Path("/tmp/key.pem")))


def test_parse_args_supports_sessions_dangerous_yolo_and_self_signed(monkeypatch):
    monkeypatch.setattr(
        cli.sys,
        "argv",
        ["yolomux.py", "--host", "0.0.0.0", "--port", "8001", "--sessions", "1,2", "ant", "--dang", "--self-signed"],
    )

    args = cli.parse_args()

    assert args.host == "0.0.0.0"
    assert args.port == 8001
    assert args.sessions == ["1,2", "ant"]
    assert args.dangerously_yolo is True
    assert args.self_signed is True


def test_main_maps_cli_flags_to_app_and_server(monkeypatch, capsys):
    captured = {}
    args = argparse.Namespace(
        host="0.0.0.0",
        port=8001,
        sessions=["1,2", "ant"],
        dangerously_yolo=True,
        self_signed=False,
        cert=None,
        key=None,
        print_transcripts=False,
        dev=False,
    )

    class FakeApp:
        def __init__(self, sessions, dangerously_yolo=False):
            captured["sessions"] = sessions
            captured["dangerously_yolo"] = dangerously_yolo

        def restore_auto_approve(self):
            return []

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
    monkeypatch.setattr(cli, "tls_context_for_args", lambda _args: (None, ""))
    monkeypatch.setattr(cli, "TmuxWebtermApp", FakeApp)
    monkeypatch.setattr(cli, "TmuxWebtermHTTPServer", FakeServer)
    monkeypatch.setattr(cli, "auth_setup_required", lambda: False)

    assert cli.main() == 0

    output = capsys.readouterr().out
    assert captured["sessions"] == ["1", "2", "ant"]
    assert captured["dangerously_yolo"] is True
    assert captured["address"] == ("0.0.0.0", 8001)
    assert captured["tls_context"] is None
    assert captured["dev"] is False  # dev mode off by default
    assert captured["served"] is True
    assert captured["stopped"] is True
    assert captured["closed"] is True
    assert "DANGEROUS YOLO mode is enabled" in output


def test_self_signed_cert_generation_is_persistent(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "STATE_DIR", tmp_path)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/openssl")
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
        b"GET /foo?bar=1 HTTP/1.1\r\nHost: localhost:8001\r\n\r\n",
        "fallback:8001",
    )

    assert b"308 Permanent Redirect" in response
    assert b"Location: https://localhost:8001/foo?bar=1" in response
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
