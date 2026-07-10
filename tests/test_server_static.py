import gzip
import io
import json
from http import HTTPStatus
from pathlib import Path
from types import SimpleNamespace

from yolomux_lib import server as server_module
from yolomux_lib import web
from yolomux_lib.server import Handler


SOURCE_STATIC_DIR = Path(__file__).resolve().parents[1] / "static_src"


def static_handler(path: str, accept_encoding: str | None = None, app=None, command: str = "GET") -> Handler:
    handler = object.__new__(Handler)
    handler.path = path
    handler.command = command
    handler.server = SimpleNamespace(app=app) if app is not None else SimpleNamespace(app=SimpleNamespace())
    handler.headers = {}
    handler.request_locale_pref = lambda: "system"
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


def test_keepalive_request_boundary_resets_response_profile_state(monkeypatch):
    handler = object.__new__(Handler)
    handler._http_response_compute_ms = 37.0
    handler._http_response_performance_details = {"html_page": True}
    observed = {}

    def base_handle_one_request(self):
        observed["compute_ms"] = self._http_response_compute_ms
        observed["details"] = self._http_response_performance_details

    monkeypatch.setattr(server_module.BaseHTTPRequestHandler, "handle_one_request", base_handle_one_request)

    handler.handle_one_request()

    assert observed == {"compute_ms": None, "details": None}


def test_static_asset_errors_localize_without_exposing_read_failure(monkeypatch, caplog):
    monkeypatch.setattr(web, "STATIC_DIR", SOURCE_STATIC_DIR)
    web.bootstrap_locale_catalogs.cache_clear()
    missing = static_handler("/static/missing.js")
    missing.headers["Accept-Language"] = "zh-CN"
    monkeypatch.setattr(server_module, "static_asset_path", lambda _asset: None)

    try:
        missing.write_static_asset("missing.js", "application/javascript; charset=utf-8")
        assert missing.sent_status == HTTPStatus.NOT_FOUND
        assert missing.wfile.getvalue().decode("utf-8") == web.server_string(
            "zh-Hans",
            "request.error.staticAssetMissing",
            asset="missing.js",
        ) + "\n"

        class UnreadableAsset:
            def read_bytes(self):
                raise OSError("private filesystem detail")

        monkeypatch.setattr(server_module, "static_asset_path", lambda _asset: UnreadableAsset())
        failed = static_handler("/static/broken.js")
        failed.headers["Accept-Language"] = "zh-CN"
        caplog.set_level("WARNING", logger="yolomux_lib.server")

        failed.write_static_asset("broken.js", "application/javascript; charset=utf-8")

        response = failed.wfile.getvalue().decode("utf-8")
        assert failed.sent_status == HTTPStatus.INTERNAL_SERVER_ERROR
        assert response == web.server_string(
            "zh-Hans",
            "request.error.staticAssetReadFailed",
            asset="broken.js",
        ) + "\n"
        assert "private filesystem detail" not in response
        assert "private filesystem detail" in caplog.text

        failed_head = static_handler("/static/broken.js", command="HEAD")
        failed_head.headers["Accept-Language"] = "zh-CN"
        failed_head.write_static_head("broken.js", "application/javascript; charset=utf-8")
        assert failed_head.sent_status == HTTPStatus.INTERNAL_SERVER_ERROR
        assert failed_head.wfile.getvalue().decode("utf-8") == web.server_string(
            "zh-Hans",
            "request.error.staticAssetReadFailed",
            asset="broken.js",
        ) + "\n"
    finally:
        web.bootstrap_locale_catalogs.cache_clear()


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
    assert json_handler.wfile.getvalue() == b'{"ok":true}'


def test_write_json_negotiates_gzip_and_wildcard_transparently():
    payload = {"rows": [{"name": "stats", "value": "x" * 120} for _index in range(80)]}
    for accept_encoding in ("br, gzip", "*;q=0.5"):
        handler = static_handler("/api/stats-sample", accept_encoding)

        handler.write_json(payload)

        headers = response_headers(handler)
        encoded = handler.wfile.getvalue()
        assert handler.sent_status == HTTPStatus.OK
        assert headers["content-encoding"] == "gzip"
        assert headers["vary"] == "Accept-Encoding"
        assert int(headers["content-length"]) == len(encoded)
        assert len(encoded) < len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        assert json.loads(gzip.decompress(encoded)) == payload


def test_write_json_keeps_small_and_explicit_gzip_q_zero_responses_uncompressed():
    cases = [
        ({"ok": True}, "gzip"),
        ({"rows": ["x" * 120 for _index in range(80)]}, "gzip;q=0, *;q=1"),
    ]
    for payload, accept_encoding in cases:
        handler = static_handler("/api/stats-sample", accept_encoding)

        handler.write_json(payload)

        headers = response_headers(handler)
        assert "content-encoding" not in headers
        assert headers["vary"] == "Accept-Encoding"
        assert json.loads(handler.wfile.getvalue()) == payload


def test_write_json_preserves_error_and_head_http_semantics_with_gzip():
    payload = {"error": "history unavailable", "details": "x" * 5000}
    error_handler = static_handler("/api/stats-sample", "gzip")
    head_handler = static_handler("/api/stats-sample", "gzip", command="HEAD")

    error_handler.write_json(payload, status=HTTPStatus.SERVICE_UNAVAILABLE)
    head_handler.write_json(payload)

    error_headers = response_headers(error_handler)
    head_headers = response_headers(head_handler)
    assert error_handler.sent_status == HTTPStatus.SERVICE_UNAVAILABLE
    assert error_headers["content-encoding"] == "gzip"
    assert json.loads(gzip.decompress(error_handler.wfile.getvalue())) == payload
    assert head_handler.sent_status == HTTPStatus.OK
    assert head_headers["content-encoding"] == "gzip"
    assert int(head_headers["content-length"]) > 0
    assert head_handler.wfile.getvalue() == b""


def test_response_writers_record_endpoint_response_bytes():
    records = []
    app = SimpleNamespace(record_performance_sample=lambda *args, **kwargs: records.append((args, kwargs)))
    handler = static_handler("/api/session-files?session=8002", app=app)

    handler.write_json({"ok": True, "files": ["a.py"]})

    assert handler.wfile.getvalue() == b'{"ok":true,"files":["a.py"]}'
    assert len(records) == 1
    args, kwargs = records[0]
    assert args == ("http-endpoint", "GET /api/session-files")
    assert kwargs["trigger"] == "GET /api/session-files"
    assert kwargs["payload_bytes"] == len(handler.wfile.getvalue())
    assert kwargs["cache_key"] == {"kind": "GET /api/session-files"}
    assert kwargs["cache_status"] == "200"
    assert kwargs["owner_role"] == "server"
    assert kwargs["details"]["status"] == 200
    assert kwargs["details"]["method"] == "GET"
    assert kwargs["details"]["path"] == "/api/session-files"
    assert kwargs["details"]["content_type"] == "application/json"
    assert kwargs["details"]["uncompressed_bytes"] == len(handler.wfile.getvalue())
    assert kwargs["details"]["wire_bytes"] == len(handler.wfile.getvalue())
    assert kwargs["details"]["representation_bytes"] == len(handler.wfile.getvalue())
    assert kwargs["details"]["content_encoding"] == "identity"
    assert kwargs["details"]["json_encode_ms"] >= 0
    assert kwargs["details"]["compression_ms"] == 0
    assert kwargs["details"]["write_ms"] >= 0


def test_gzipped_json_endpoint_profile_records_uncompressed_wire_compression_and_write_phases():
    records = []
    app = SimpleNamespace(record_performance_sample=lambda *args, **kwargs: records.append((args, kwargs)))
    handler = static_handler("/api/stats-sample", "gzip", app=app)
    payload = {"records": [{"value": "x" * 200} for _index in range(100)]}

    handler.write_json(payload)

    assert len(records) == 1
    _args, kwargs = records[0]
    details = kwargs["details"]
    assert details["content_encoding"] == "gzip"
    assert details["uncompressed_bytes"] > details["wire_bytes"] == len(handler.wfile.getvalue())
    assert details["representation_bytes"] == details["wire_bytes"]
    assert details["compression_ms"] >= 0
    assert details["json_encode_ms"] >= 0
    assert details["write_ms"] >= 0
