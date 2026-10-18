"""Shared semantic HTTP test seams."""
from http import HTTPStatus
from http.client import HTTPConnection
import io
import json
from types import MethodType, SimpleNamespace
from urllib.parse import urlencode

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
    return next(value.split(";", 1)[0] for value in cookies if "yomux_auth_" in value and "Max-Age=0" not in value)
