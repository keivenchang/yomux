"""Typed loopback HTTP client and server lifetime for socket tests."""
from __future__ import annotations

from dataclasses import dataclass
from http.client import HTTPConnection
from threading import Thread
from typing import Mapping
from typing import Sequence

from yolomux_lib.server import TmuxWebtermHTTPServer


@dataclass(frozen=True)
class FixtureHttpResponse:
    """One fully consumed response, retaining repeated header fields in wire order."""

    status: int
    headers: tuple[tuple[str, str], ...]
    body: bytes

    def header_map(self) -> dict[str, str]:
        return dict(self.headers)

    def as_tuple(self) -> tuple[int, dict[str, str], bytes]:
        return self.status, self.header_map(), self.body

    def as_header_list(self) -> tuple[int, list[tuple[str, str]], bytes]:
        return self.status, list(self.headers), self.body


def request_fixture_http(
    port: int,
    method: str,
    path: str,
    body: bytes | str | None = None,
    headers: Mapping[str, str] | None = None,
) -> FixtureHttpResponse:
    """Issue one loopback request and consume its response before closing."""

    connection = HTTPConnection("127.0.0.1", port, timeout=5)
    connection.request(method, path, body=body, headers=dict(headers or {}))
    response = connection.getresponse()
    response_body = response.read()
    result = FixtureHttpResponse(response.status, tuple(response.getheaders()), response_body)
    connection.close()
    return result


def request_fixture_http_tuple(
    port: int,
    method: str,
    path: str,
    body: bytes | str | None = None,
    headers: Mapping[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    return request_fixture_http(port, method, path, body, headers).as_tuple()


def request_fixture_http_header_list(
    port: int,
    method: str,
    path: str,
    body: bytes | str | None = None,
    headers: Mapping[str, str] | None = None,
) -> tuple[int, list[tuple[str, str]], bytes]:
    return request_fixture_http(port, method, path, body, headers).as_header_list()


@dataclass
class FixtureHttpServer:
    """Own one ephemeral loopback server and its serving thread."""

    server: TmuxWebtermHTTPServer
    thread: Thread
    label: str

    @classmethod
    def start(
        cls,
        app: object,
        *,
        tls_context: object | None = None,
        label: str = "fixture HTTP server",
        thread_name: str = "fixture-http-server",
    ) -> FixtureHttpServer:
        server = TmuxWebtermHTTPServer(("127.0.0.1", 0), app, tls_context=tls_context)
        thread = Thread(target=server.serve_forever, name=thread_name, daemon=True)
        thread.start()
        return cls(server, thread, label)

    @property
    def app(self) -> object:
        return self.server.app

    @property
    def port(self) -> int:
        return int(self.server.server_address[1])

    def request(
        self,
        method: str,
        path: str,
        body: bytes | str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> FixtureHttpResponse:
        return request_fixture_http(self.port, method, path, body, headers)

    def close(self) -> None:
        """Run shutdown, close, and join in order; preserve the first failure."""

        errors: list[BaseException] = []
        for callback in (self.server.shutdown, self.server.server_close, self._join_thread):
            try:
                callback()
            except BaseException as error:
                errors.append(error)
        if errors:
            raise errors[0]

    def _join_thread(self) -> None:
        self.thread.join(timeout=2)
        assert not self.thread.is_alive(), f"{self.label} thread did not stop"

    def __enter__(self) -> FixtureHttpServer:
        return self

    def __exit__(self, _error_type, _error, _traceback) -> None:
        self.close()


def header_values(headers: Sequence[tuple[str, str]], name: str) -> tuple[str, ...]:
    """Return every case-insensitive header occurrence without collapsing duplicates."""

    lowered = name.lower()
    return tuple(value for header_name, value in headers if header_name.lower() == lowered)
