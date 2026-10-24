# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Browser credentials must not authorize another site's state changes."""

import ssl
import subprocess
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from urllib.parse import urlencode

import pytest

from tests.browser_helpers.browser_layout import browser  # noqa: F401
from tests.browser_helpers.browser_layout import browser_auth_yaml
from tests.browser_helpers.browser_layout import register_browser_new_document_script
from tests.browser_helpers.browser_console import assert_only_expected_browser_http_error
from tests.isolated_dev_server import isolated_dev_server_factory  # noqa: F401


class PrivateOriginHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<html><body>Private browser origin</body></html>")

    def log_message(self, *_args):
        pass


@pytest.fixture(autouse=True)
def retire_origin_page_before_server(browser, isolated_dev_server_factory):
    # An HTTP error document has no icon declaration; own its favicon request as
    # fixture chrome so it cannot add an unrelated 404 to deliberate 403/405s.
    register_browser_new_document_script(browser, """
      document.addEventListener('DOMContentLoaded', () => {
        const icon = document.createElement('link');
        icon.rel = 'icon'; icon.href = 'data:,'; document.head.appendChild(icon);
      }, {once:true});
    """)
    yield
    browser.get("about:blank")


@contextmanager
def private_browser_origin(server):
    origin = ThreadingHTTPServer(("127.0.0.1", 0), PrivateOriginHandler)
    if server.tls:
        certificate = server.paths.tmp_dir / f"yomux-go-tls-{server.port}" / "self-signed.crt"
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(certificate), str(certificate.with_name("self-signed.key")))
        origin.socket = context.wrap_socket(origin.socket, server_side=True)
    worker = threading.Thread(target=origin.serve_forever, daemon=True)
    worker.start()
    try:
        yield ("https" if server.tls else "http") + f"://127.0.0.1:{origin.server_port}"
    finally:
        origin.shutdown()
        origin.server_close()
        worker.join(timeout=5)
        assert worker.is_alive() is False


def authenticate_browser(browser, server):
    browser.get(server.base_url + "/login")
    status = browser.execute_async_script("""
      const done = arguments[arguments.length - 1];
      fetch('/api/ping', {headers:{Authorization:'Basic ' + btoa('keivenc:random-password')}})
        .then(response => done(response.status), error => done(String(error)));
    """)
    assert status == 200


def browser_theme(browser, server):
    browser.get(server.base_url + "/login")
    return browser.execute_async_script("""
      const done = arguments[arguments.length - 1];
      fetch('/api/settings').then(response => response.json())
        .then(body => done(body.settings.appearance.theme), error => done(String(error)));
    """)


def cross_origin_theme_write(browser, server, theme):
    result = browser.execute_async_script("""
      const done = arguments[arguments.length - 1];
      fetch(arguments[0] + '/api/settings', {
        method:'POST', mode:'no-cors', credentials:'include',
        headers:{'Content-Type':'text/plain'},
        body:JSON.stringify({settings:{appearance:{theme:arguments[1]}}}),
        signal:AbortSignal.timeout(5000),
      }).then(response => done(response.type), error => done(String(error)));
    """, server.base_url, theme)
    assert result == "opaque", result
    assert_only_expected_browser_http_error(browser, path="/api/settings", status=403, query={})


@pytest.mark.parametrize("tls", (False, True))
def test_native_browser_other_origin_cannot_change_settings(browser, isolated_dev_server_factory, tls):
    server = isolated_dev_server_factory("browser-origin", tls=tls, auth_bypass=False)
    server.paths.auth_config_path.write_text(browser_auth_yaml(), encoding="utf-8")
    authenticate_browser(browser, server)
    initial = browser_theme(browser, server)
    desired = "light" if initial != "light" else "dark"
    with private_browser_origin(server) as origin:
        browser.get(origin.replace("127.0.0.1", "localhost"))
        cross_origin_theme_write(browser, server, desired)
        assert browser_theme(browser, server) == initial
        browser.get(origin)
        cross_origin_theme_write(browser, server, desired)
        assert browser_theme(browser, server) == initial
    result = browser.execute_async_script("""
      const done = arguments[arguments.length - 1];
      fetch('/api/settings', {method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({settings:{appearance:{theme:arguments[0]}}})})
        .then(response => done(response.status), error => done(String(error)));
    """, desired)
    assert result == 200
    assert browser_theme(browser, server) == desired


def test_native_cross_site_navigation_cannot_kill_session(browser, isolated_dev_server_factory):
    server = isolated_dev_server_factory("browser-navigation", tls=True, auth_bypass=False, session_count=2)
    server.paths.auth_config_path.write_text(browser_auth_yaml(), encoding="utf-8")
    authenticate_browser(browser, server)
    session = server.tmux.sessions[0]
    command = [server.tmux.tmux_binary, *server.tmux.tmux_args, "has-session", "-t", session]
    assert subprocess.run(command, capture_output=True, check=False).returncode == 0
    with private_browser_origin(server) as origin:
        browser.get(origin.replace("127.0.0.1", "localhost"))
        browser.execute_script("""
          const link = document.createElement('a');
          link.id = 'fixture-navigation'; link.textContent = 'Follow';
          link.href = arguments[0]; document.body.appendChild(link);
        """, server.base_url + "/api/kill-session?" + urlencode({"session": session}))
        browser.find_element("id", "fixture-navigation").click()
        assert subprocess.run(command, capture_output=True, check=False).returncode == 0
        assert_only_expected_browser_http_error(browser, path="/api/kill-session", status=405, query={"session": session})
    assert "method not allowed" in browser.find_element("tag name", "body").text.lower()
