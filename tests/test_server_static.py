import gzip
import io
from http import HTTPStatus

from yolomux_lib import server as server_module
from yolomux_lib.server import Handler


def static_handler(path: str, accept_encoding: str | None = None) -> Handler:
    handler = object.__new__(Handler)
    handler.path = path
    handler.headers = {}
    if accept_encoding is not None:
        handler.headers["Accept-Encoding"] = accept_encoding
    handler.close_connection = False
    handler.wfile = io.BytesIO()
    handler.sent_status = None
    handler.sent_headers = []
    handler.auth_cookie_calls = 0
    handler.ended_headers = False
    handler.send_response = lambda status: setattr(handler, "sent_status", status)
    handler.send_header = lambda name, value: handler.sent_headers.append((name, value))
    handler.end_headers = lambda: setattr(handler, "ended_headers", True)
    handler.send_auth_cookie_if_needed = lambda: setattr(handler, "auth_cookie_calls", handler.auth_cookie_calls + 1)
    return handler


def response_headers(handler: Handler) -> dict[str, str]:
    return {name.lower(): value for name, value in handler.sent_headers}


def test_write_static_asset_gzips_and_caches_versioned_assets(monkeypatch, tmp_path):
    body = b"function boot() { return 42; }\n" * 200
    asset_path = tmp_path / "yolomux.js"
    asset_path.write_bytes(body)
    monkeypatch.setattr(server_module, "static_asset_path", lambda asset: asset_path if asset == "yolomux.js" else None)
    handler = static_handler("/static/yolomux.js?v=123", "br, gzip")

    handler.write_static_asset("yolomux.js", "application/javascript; charset=utf-8")

    headers = response_headers(handler)
    encoded = handler.wfile.getvalue()
    assert handler.sent_status == HTTPStatus.OK
    assert headers["content-encoding"] == "gzip"
    assert headers["vary"] == "Accept-Encoding"
    assert headers["cache-control"] == "public, max-age=31536000, immutable"
    assert int(headers["content-length"]) == len(encoded)
    assert len(encoded) < len(body)
    assert gzip.decompress(encoded) == body


def test_write_static_asset_respects_gzip_q_zero_and_unversioned_no_store(monkeypatch, tmp_path):
    body = b".terminal { color: var(--fg); }\n" * 200
    asset_path = tmp_path / "yolomux.css"
    asset_path.write_bytes(body)
    monkeypatch.setattr(server_module, "static_asset_path", lambda asset: asset_path if asset == "yolomux.css" else None)
    handler = static_handler("/static/yolomux.css", "br, gzip;q=0")

    handler.write_static_asset("yolomux.css", "text/css; charset=utf-8")

    headers = response_headers(handler)
    assert handler.sent_status == HTTPStatus.OK
    assert "content-encoding" not in headers
    assert headers["vary"] == "Accept-Encoding"
    assert headers["cache-control"] == "no-store"
    assert int(headers["content-length"]) == len(body)
    assert handler.wfile.getvalue() == body


def test_write_static_head_uses_negotiated_static_headers_without_body(monkeypatch, tmp_path):
    body = b"window.dockview = true;\n" * 200
    asset_path = tmp_path / "dockview-core.noStyle.js"
    asset_path.write_bytes(body)
    monkeypatch.setattr(server_module, "static_asset_path", lambda asset: asset_path if asset == "vendor/dockview-core.noStyle.js" else None)
    handler = static_handler("/static/vendor/dockview-core.noStyle.js?v=456", "gzip")

    handler.write_static_head("vendor/dockview-core.noStyle.js", "application/javascript; charset=utf-8")

    headers = response_headers(handler)
    assert handler.sent_status == HTTPStatus.OK
    assert headers["content-encoding"] == "gzip"
    assert headers["vary"] == "Accept-Encoding"
    assert headers["cache-control"] == "public, max-age=31536000, immutable"
    assert int(headers["content-length"]) == len(gzip.compress(body, compresslevel=6, mtime=0))
    assert handler.wfile.getvalue() == b""


def test_html_and_json_responses_remain_no_store():
    json_handler = static_handler("/api/ping")
    html_handler = static_handler("/")

    json_handler.write_json({"ok": True})
    html_handler.write_html("<!doctype html><main>ok</main>")

    json_headers = response_headers(json_handler)
    html_headers = response_headers(html_handler)
    assert json_headers["cache-control"] == "no-store"
    assert html_headers["cache-control"] == "no-store"
    assert "content-encoding" not in json_headers
    assert "content-encoding" not in html_headers
