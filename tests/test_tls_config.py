import argparse
import subprocess
from pathlib import Path

import pytest

from yolomux_lib import cli
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
        b"GET /foo?bar=1 HTTP/1.1\r\nHost: localhost:7778\r\n\r\n",
        "fallback:7778",
    )

    assert b"308 Permanent Redirect" in response
    assert b"Location: https://localhost:7778/foo?bar=1" in response
    assert b"Use HTTPS" in response
