"""Shared semantic HTTP test seams."""
from http import HTTPStatus
from http.client import HTTPConnection
import io
import json
from types import MethodType, SimpleNamespace
from urllib.parse import urlencode
from yolomux_lib import http_routes
from yolomux_lib import server

def login_cookie(runtime, credentials) -> str:
    body = urlencode({"username": credentials.username, "password": credentials.password, "next": "/api/ping"}).encode()
    connection = HTTPConnection("127.0.0.1", runtime.port, timeout=8)
    try:
        connection.request("POST", "/login", body=body, headers={"Content-Type": "application/x-www-form-urlencoded", "Content-Length": str(len(body)), "Connection": "close"})
        response = connection.getresponse(); response.read()
        cookies = [value for name, value in response.getheaders() if name.lower() == "set-cookie"]
    finally:
        connection.close()
    assert response.status == 303, response.status
    return next(value.split(";", 1)[0] for value in cookies if "yolomux_auth_" in value and "Max-Age=0" not in value)

def capturing_route_request(app, path: str, method: str = "GET", body: bytes = b""):
    handler = server.Handler.__new__(server.Handler)
    handler.path = path; handler._route_response = None; handler._route_response_written = False; handler._api_request_id = ""
    handler.headers = {"Content-Length": str(len(body))} if body else {}; handler.rfile = io.BytesIO(body)
    handler.server = SimpleNamespace(app=app, dev=False); handler.close_connection = False
    handler.require_auth = lambda role="readonly": True; handler.auth_readonly = lambda: False
    handler.auth_identity = lambda: SimpleNamespace(role="admin", username="tester")
    handler.redirect_plaintext_to_https_if_needed = lambda parsed: False
    writes = []
    def capture(_self, data, status=HTTPStatus.OK, *, json_encode_ms=0.0, product_metadata=None):
        del json_encode_ms, product_metadata
        writes.append((json.loads(data), HTTPStatus(int(status))))
    handler._write_json_representation = MethodType(capture, handler)
    return lambda: http_routes.dispatch_http_route(handler, method), writes
