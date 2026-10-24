# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Real Chromium transport contracts against a private native HTTPS listener."""

import socket
import ssl

from tests.browser_helpers.browser_layout import browser  # noqa: F401
from tests.browser_helpers.browser_layout import browser_auth_yaml
from tests.isolated_dev_server import isolated_dev_server_factory  # noqa: F401


def test_native_tls_multiplexes_authenticated_streams_and_keeps_websocket(browser, isolated_dev_server_factory):
    server = isolated_dev_server_factory("native-tls", tls=True, auth_bypass=False)
    server.paths.auth_config_path.write_text(browser_auth_yaml(), encoding="utf-8")
    unauthorized_status, _, _ = server.request("/api/ping")
    assert unauthorized_status == 401
    certificate = server.paths.tmp_dir / f"yomux-go-tls-{server.port}" / "self-signed.crt"
    context = ssl.create_default_context(cafile=str(certificate))
    context.set_alpn_protocols(["h2", "http/1.1"])
    with socket.create_connection(("127.0.0.1", server.port), timeout=5) as connection:
        with context.wrap_socket(connection, server_hostname="127.0.0.1") as secured:
            negotiated = secured.selected_alpn_protocol()

    try:
        browser.get(server.base_url + "/login")
        metrics = browser.execute_async_script("""
      const done = arguments[arguments.length - 1];
      const session = arguments[0];
      (async () => {
        const auth = await fetch('/api/ping', {
          headers: {Authorization: 'Basic ' + btoa('keivenc:random-password')},
          signal: AbortSignal.timeout(5000),
        });
        if (!auth.ok) throw new Error('fixture authentication failed: ' + auth.status);
        await auth.json();
        window.__tlsFixtureStreams = [];
        const opened = await Promise.all(Array.from({length:6}, (_,index) => new Promise((resolve,reject) => {
          const stream = new EventSource('/api/client-events?client_id=tls-fixture-' + index);
          window.__tlsFixtureStreams.push(stream);
          stream.addEventListener('ready', () => resolve(stream.readyState), {once:true});
          stream.onerror = () => reject(new Error('fixture stream failed: ' + index));
        })));
        const started = performance.now();
        let ping;
        try {
          const response = await fetch('/api/ping?probe=tls-multiplex', {
            cache:'no-store', signal:AbortSignal.timeout(5000),
          });
          await response.json();
          ping = {ok:response.ok, elapsedMs:performance.now()-started};
        } catch (error) {
          ping = {ok:false, error:String(error), elapsedMs:performance.now()-started};
        }
        const retained = window.__tlsFixtureStreams.map(stream => stream.readyState);
        const websocket = await new Promise((resolve,reject) => {
          const connection = new WebSocket(location.origin.replace('https:', 'wss:') + '/ws?session=' + encodeURIComponent(session));
          const deadline = setTimeout(() => { connection.close(); reject(new Error('fixture WebSocket open deadline')); },5000);
          let opened = false;
          connection.onopen = () => { opened = true; connection.close(); };
          connection.onclose = () => { clearTimeout(deadline); resolve(opened); };
          connection.onerror = () => { clearTimeout(deadline); reject(new Error('fixture WebSocket failed')); };
        });
        return {opened, retained, ping, websocket,
          protocol:performance.getEntriesByType('navigation')[0].nextHopProtocol};
      })().then(done, error => done({error:String(error.stack || error)}));
        """, server.tmux.sessions[0])
        cookies = browser.get_cookies()
        auth_cookies = [cookie for cookie in cookies if cookie["name"].startswith("yomux_auth")]
        safe_auth_cookie = bool(auth_cookies) and all(cookie["secure"] and cookie["httpOnly"] for cookie in auth_cookies)
        assert safe_auth_cookie is True
        cookie_header = "; ".join(cookie["name"] + "=" + cookie["value"] for cookie in cookies)
        context.set_alpn_protocols(["http/1.1"])
        with socket.create_connection(("127.0.0.1", server.port), timeout=5) as connection:
            with context.wrap_socket(connection, server_hostname="127.0.0.1") as secured:
                websocket_negotiated = secured.selected_alpn_protocol()
                request = (
                    f"GET /ws?session={server.tmux.sessions[0]} HTTP/1.1\r\nHost: 127.0.0.1:{server.port}\r\n"
                    f"Origin: {server.base_url}\r\nCookie: {cookie_header}\r\n"
                    "Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Version: 13\r\n"
                    "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n"
                )
                secured.sendall(request.encode())
                headers = b""
                while b"\r\n\r\n" not in headers:
                    chunk = secured.recv(4096)
                    assert chunk, "native WebSocket closed before its HTTP/1.1 upgrade"
                    headers += chunk
                assert headers.startswith(b"HTTP/1.1 101 Switching Protocols\r\n")
                secured.sendall(b"\x88\x80\x00\x00\x00\x00")
    finally:
        browser.execute_script("for (const stream of window.__tlsFixtureStreams || []) stream.close();")
        server.stop()
    assert "error" not in metrics, metrics
    assert metrics["opened"] == [1] * 6 and metrics["retained"] == [1] * 6, metrics
    assert metrics["ping"]["ok"] is True, metrics
    assert negotiated == "h2" and metrics["protocol"] == "h2", metrics
    assert websocket_negotiated == "http/1.1"
    assert metrics["websocket"] is True, metrics
    assert server.process.returncode == 0, server.output
