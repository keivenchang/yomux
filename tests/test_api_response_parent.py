# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Registry-derived guard for the shared API response parent."""

from __future__ import annotations

import ast
import errno
import hashlib
import io
import inspect
import json
from contextlib import nullcontext
from http import HTTPStatus
from pathlib import Path
from types import MethodType
from types import SimpleNamespace

import pytest

from yomux_lib import app as app_module
from yomux_lib import http_routes
from yomux_lib import server
from yomux_lib import server_logs
from yomux_lib.local_services import client as local_service_client_module
from yomux_lib.local_services.client import LocalServiceClient
from yomux_lib.local_services.client import local_service_failure_is_transient
from yomux_lib.observability.queued_delivery import QueuedDeliveryLedger


def _route_registrations() -> list[ast.Call]:
    tree = ast.parse(inspect.getsource(http_routes))
    registrations = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "Route"
    ]
    assert len(registrations) == len(http_routes.ALL_ROUTES)
    return registrations


def _assert_registry_uses_response_parent(route_source: str, server_source: str) -> None:
    route_tree = ast.parse(route_source)
    registrations = [
        node
        for node in ast.walk(route_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "Route"
    ]
    missing_protocol = [
        node.lineno
        for node in registrations
        if not any(keyword.arg == "protocol" for keyword in node.keywords)
    ]
    assert not missing_protocol, f"Route registrations missing protocol=: {missing_protocol}"

    route_functions = {
        node.name: node
        for node in route_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    dispatch = next(
        node
        for node in ast.walk(route_tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "dispatch_http_route"
    )
    dispatch_calls = [
        node
        for node in ast.walk(dispatch)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "dispatch_route_response"
    ]
    assert len(dispatch_calls) == 1, "dispatch_http_route must use dispatch_route_response exactly once"

    server_tree = ast.parse(server_source)
    handler_class = next(
        node
        for node in server_tree.body
        if isinstance(node, ast.ClassDef) and node.name == "Handler"
    )
    handler_methods = {
        node.name: node
        for node in handler_class.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    adapter_methods = {
        class_node.name: {
            node.name: node
            for node in class_node.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for class_node in server_tree.body
        if isinstance(class_node, ast.ClassDef) and class_node.name.endswith("Adapter")
    }
    parent_writers = {
        "write_app_result",
        "write_int_query_app_result",
        "write_json",
        "write_json_bytes",
        "write_product_bytes",
        "write_validated_float_result",
        "write_validated_int_result",
    }
    bypass_writers = {
        "_write_bodyless_api_response",
        "_write_json_representation",
        "end_headers",
        "send_response",
        "write_html",
        "write_redirect",
        "write_sse_bytes",
        "write_sse_json",
        "write_static_asset",
        "write_text",
    }

    def response_paths(kind: str, name: str, seen: frozenset[tuple[str, str]]) -> tuple[set[str], set[str]]:
        key = (kind, name)
        if key in seen:
            return set(), set()
        functions = route_functions if kind == "route" else handler_methods if kind == "handler" else adapter_methods[kind]
        function = functions.get(name)
        if function is None:
            return set(), set()
        next_seen = seen | {key}
        parents: set[str] = set()
        bypasses: set[str] = set()
        owner = "request" if kind == "route" else "self"
        for call in (node for node in ast.walk(function) if isinstance(node, ast.Call)):
            if (
                isinstance(call.func, ast.Attribute)
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == owner
            ):
                called = call.func.attr
                if called in parent_writers:
                    parents.add(called)
                elif called in bypass_writers:
                    bypasses.add(called)
                elif called in handler_methods:
                    nested_parents, nested_bypasses = response_paths("handler", called, next_seen)
                    parents.update(nested_parents)
                    bypasses.update(nested_bypasses)
            elif (
                kind != "route"
                and isinstance(call.func, ast.Attribute)
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id in adapter_methods
                and call.func.attr in adapter_methods[call.func.value.id]
            ):
                nested_parents, nested_bypasses = response_paths(call.func.value.id, call.func.attr, next_seen)
                parents.update(nested_parents)
                bypasses.update(nested_bypasses)
            elif kind == "route" and isinstance(call.func, ast.Name) and call.func.id in route_functions:
                nested_parents, nested_bypasses = response_paths("route", call.func.id, next_seen)
                parents.update(nested_parents)
                bypasses.update(nested_bypasses)
        return parents, bypasses

    for registration in registrations:
        keywords = {keyword.arg: keyword.value for keyword in registration.keywords}
        protocol = keywords.get("protocol")
        if not isinstance(protocol, ast.Name) or protocol.id not in {"RESPONSE_JSON", "RESPONSE_JSON_BATCH"}:
            continue
        handler = registration.args[3]
        assert isinstance(handler, ast.Name), f"JSON route at line {registration.lineno} has a non-name handler"
        parents, bypasses = response_paths("route", handler.id, frozenset())
        assert parents and not bypasses, (
            f"registered JSON route bypasses response parent: {handler.id}; "
            f"parents={sorted(parents)} bypasses={sorted(bypasses)}"
        )

    dispatch = next(
        node
        for node in ast.walk(server_tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "dispatch_route_response"
    )
    parent_calls = [
        node
        for node in ast.walk(dispatch)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "write_api_response"
    ]
    assert parent_calls, "dispatch_route_response must emit failures through write_api_response"

    for writer_name in ("write_json", "write_json_bytes", "write_product_bytes"):
        writer = next(
            node
            for node in ast.walk(server_tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == writer_name
        )
        calls = [
            node
            for node in ast.walk(writer)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "write_api_response"
        ]
        assert calls, f"{writer_name} bypasses write_api_response"

    representation_callers = []
    for function in (
        node
        for node in ast.walk(server_tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ):
        if any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_write_json_representation"
            for node in ast.walk(function)
        ) and function.name != "_write_json_representation":
            representation_callers.append(function.name)
    assert representation_callers == ["write_api_response"], (
        "JSON representation bypasses write_api_response: "
        f"{representation_callers}"
    )

    bodyless_callers = []
    for function in (
        node
        for node in ast.walk(server_tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ):
        if any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_write_bodyless_api_response"
            for node in ast.walk(function)
        ) and function.name != "_write_bodyless_api_response":
            bodyless_callers.append(function.name)
    assert bodyless_callers == ["write_api_response"], (
        "Bodyless API representation bypasses write_api_response: "
        f"{bodyless_callers}"
    )


def test_every_registered_route_declares_protocol_and_json_writers_share_one_parent() -> None:
    registrations = _route_registrations()
    assert registrations
    _assert_registry_uses_response_parent(inspect.getsource(http_routes), inspect.getsource(server))


def test_response_parent_guard_proves_a_writer_bypass_is_red() -> None:
    server_source = inspect.getsource(server)
    bypassed = server_source.replace(
        "self.write_api_response(value, status=status)",
        "self._write_json_representation(value, status=status)",
        1,
    )
    assert bypassed != server_source
    with pytest.raises(AssertionError, match="write_json bypasses"):
        _assert_registry_uses_response_parent(inspect.getsource(http_routes), bypassed)


@pytest.mark.parametrize(
    ("response", "expected"),
    (
        ({"ok": False, "_transport_error": "timeout", "error": "timed out"}, True),
        ({"ok": False, "_transport_error": "absent"}, True),
        ({"ok": False, "_transport_error": "refused"}, True),
        ({"ok": False, "_transport_error": "rpc", "error": "response exceeded deadline"}, True),
        ({"ok": False, "status": HTTPStatus.SERVICE_UNAVAILABLE, "error": "refreshing"}, True),
        ({"ok": False, "status": HTTPStatus.SERVICE_UNAVAILABLE, "error": "service busy"}, True),
        ({"ok": False, "terminal": True, "status": HTTPStatus.SERVICE_UNAVAILABLE}, False),
        ({"ok": False, "status": HTTPStatus.UPGRADE_REQUIRED, "error": "upgrade required"}, False),
        ({"ok": False, "_transport_error": "rpc", "error": "invalid response"}, False),
        ({"ok": False, "status": HTTPStatus.BAD_REQUEST, "error": "invalid request"}, False),
    ),
)
def test_shared_local_service_transient_classifier_excludes_terminal_failures(response, expected) -> None:
    assert local_service_failure_is_transient(response) is expected


def test_response_parent_guard_proves_a_registered_route_bypass_is_red() -> None:
    route_source = inspect.getsource(http_routes)
    bypassed = route_source.replace(
        'request.write_json({"setup_required": auth_setup_required()})',
        'request.write_text("setup")',
        1,
    )
    assert bypassed != route_source
    with pytest.raises(AssertionError, match="registered JSON route bypasses response parent: get_auth_setup"):
        _assert_registry_uses_response_parent(bypassed, inspect.getsource(server))


def test_public_route_fallback_uses_the_registered_method(monkeypatch: pytest.MonkeyPatch) -> None:
    route = http_routes.Route(
        "GET",
        "/static/*",
        http_routes.PUBLIC,
        lambda *_args: False,
        protocol=http_routes.RESPONSE_STATIC,
    )
    calls: list[tuple[object, str]] = []
    request = SimpleNamespace()
    monkeypatch.setattr(
        http_routes,
        "_write_not_found_after_default_auth",
        lambda routed_request, method: calls.append((routed_request, method)),
    )

    http_routes._dispatch_route_handler(request, SimpleNamespace(), route)

    assert calls == [(request, "GET")]


def _capturing_handler(route: http_routes.Route) -> tuple[server.Handler, list[tuple[dict, HTTPStatus]]]:
    handler = server.Handler.__new__(server.Handler)
    handler._route_response = route
    handler._route_response_written = False
    handler._api_request_id = ""
    handler.headers = {}
    handler.server = SimpleNamespace(app=SimpleNamespace(
        observe_http_commit=lambda *_args: None,
        observe_http_receipt=lambda *_args: None,
    ))
    writes: list[tuple[dict, HTTPStatus]] = []

    def capture(
        _self,
        data: bytes,
        status: HTTPStatus = HTTPStatus.OK,
        *,
        json_encode_ms: float = 0.0,
        product_metadata=None,
    ) -> None:
        del json_encode_ms, product_metadata
        writes.append((json.loads(data), status))

    handler._write_json_representation = MethodType(capture, handler)
    return handler, writes


def test_response_parent_wraps_ready_data_and_keeps_legacy_success_aliases() -> None:
    route = http_routes.Route(
        "GET",
        "/api/fixture",
        "readonly",
        lambda *_args: None,
        protocol=http_routes.RESPONSE_JSON,
    )
    handler, writes = _capturing_handler(route)

    handler.write_json({"answer": 42})

    payload, status = writes[0]
    assert status == HTTPStatus.OK
    assert payload["state"] == "ready"
    assert payload["request"]["id"].startswith("r-")
    assert payload["data"] == {"answer": 42}
    assert payload["answer"] == 42
    assert payload["ok"] is True and payload["terminal"] is True


def test_response_parent_same_caller_request_id_frames_identical_retained_product_bytes() -> None:
    route = http_routes.Route(
        "GET",
        "/api/fixture",
        "readonly",
        lambda *_args: None,
        protocol=http_routes.RESPONSE_JSON,
    )
    first, _first_writes = _capturing_handler(route)
    second, _second_writes = _capturing_handler(route)
    headers = {"X-YOmux-Request-ID": "r-same-request-id-parity"}
    first.headers = headers
    second.headers = headers
    product = b'{"cache_generation":7,"source_generation":5}'

    first_id = first.api_request_id()
    second_id = second.api_request_id()

    assert first_id == second_id == "r-same-request-id-parity"
    assert server.ready_response_envelope_bytes(
        product,
        first_id,
    ) == server.ready_response_envelope_bytes(product, second_id)

    first_payload = json.loads(server.ready_response_envelope_bytes(product, "r-first"))
    second_payload = json.loads(server.ready_response_envelope_bytes(product, "r-second"))
    assert first_payload["request"] == {"id": "r-first"}
    assert second_payload["request"] == {"id": "r-second"}
    first_payload["request"] = second_payload["request"]
    assert first_payload == second_payload


def test_response_parent_frames_opaque_product_bytes_without_materialization_and_keeps_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    route = http_routes.Route(
        "GET",
        "/api/fixture",
        "readonly",
        lambda *_args: None,
        protocol=http_routes.RESPONSE_JSON,
    )
    handler = server.Handler.__new__(server.Handler)
    handler._route_response = route
    handler._route_response_written = False
    handler._api_request_id = "r-opaque-product"
    handler.headers = {}
    writes = []
    written_metadata = []
    lifecycle = []
    product = b'{"sessions":{"1":{"label":"\\u2603"}},"session_order":["1"]}'
    metadata = {
        "format": "json",
        "content_type": "application/json; charset=utf-8",
        "length": len(product),
        "sha256": hashlib.sha256(product).hexdigest(),
        "disposition": "inline",
        "filename": "",
    }
    expected = (
        b'{"state":"ready","request":{"id":"r-opaque-product"},"data":'
        + product
        + b',"ok":true,"terminal":true,"sessions":{"1":{"label":"\\u2603"}},"session_order":["1"]}'
    )

    def capture(
        _self,
        data: bytes,
        status: HTTPStatus = HTTPStatus.OK,
        *,
        json_encode_ms: float = 0.0,
        product_metadata=None,
    ) -> None:
        lifecycle.append("write")
        writes.append((data, status, json_encode_ms))
        written_metadata.append(product_metadata)

    def observe_product_delivery(key: str, epoch: int) -> None:
        lifecycle.append(("ledger", key, epoch))

    frame_product = server.ready_response_envelope_bytes

    def frame(data: bytes, request_id: str) -> bytes:
        lifecycle.append("frame")
        return frame_product(data, request_id)

    def fail_materialization(*_args, **_kwargs):
        raise AssertionError("opaque product bytes must not be decoded, copied, or encoded")

    handler.server = SimpleNamespace(app=SimpleNamespace(observe_http_product_delivery=observe_product_delivery))
    handler._write_json_representation = MethodType(capture, handler)
    monkeypatch.setattr(server, "ready_response_envelope_bytes", frame)
    monkeypatch.setattr(server.json, "loads", fail_materialization)
    monkeypatch.setattr(server.json, "dumps", fail_materialization)
    monkeypatch.setattr(server.copy, "deepcopy", fail_materialization)

    handler.write_product_bytes(product, metadata, promise=("activity-summary", 7))

    assert writes == [(expected, HTTPStatus.OK, 0.0)]
    assert written_metadata == [metadata]
    assert product in writes[0][0]
    assert lifecycle == [("ledger", "activity-summary", 7), "frame", "write"]
    assert handler._route_response_written is True


def test_auto_approve_success_forwards_statusd_product_without_materialization(monkeypatch: pytest.MonkeyPatch) -> None:
    route = next(route for route in http_routes.ALL_ROUTES if route.path == "/api/auto-approve")
    handler = server.Handler.__new__(server.Handler)
    handler._route_response = route
    handler._route_response_written = False
    handler._api_request_id = "r-auto-approve-product"
    handler.headers = {}
    body = b'{"sessions":{"1":{"status":"idle"}},"agent_window_snapshot_revision":7}'
    handler.server = SimpleNamespace(app=SimpleNamespace(
        auto_approve_status_bytes=lambda session: (body, HTTPStatus.OK),
        observe_http_product_delivery=lambda *_args: None,
    ))
    writes = []

    def capture(
        _self,
        data: bytes,
        status: HTTPStatus = HTTPStatus.OK,
        *,
        json_encode_ms: float = 0.0,
        product_metadata=None,
    ) -> None:
        writes.append((data, status, json_encode_ms, product_metadata))

    def fail_materialization(*_args, **_kwargs):
        raise AssertionError("successful statusd bytes must not be decoded, copied, or encoded")

    handler._write_json_representation = MethodType(capture, handler)
    monkeypatch.setattr(server.json, "loads", fail_materialization)
    monkeypatch.setattr(server.json, "dumps", fail_materialization)
    monkeypatch.setattr(server.copy, "deepcopy", fail_materialization)

    http_routes.get_auto_approve(handler, SimpleNamespace(query="session=1"), route)

    expected = (
        b'{"state":"ready","request":{"id":"r-auto-approve-product"},"data":'
        + body
        + b',"ok":true,"terminal":true,"sessions":{"1":{"status":"idle"}},"agent_window_snapshot_revision":7}'
    )
    assert writes == [(expected, HTTPStatus.OK, 0.0, http_routes.inline_json_product_metadata(body))]


def test_auto_approve_failure_retains_canonical_transient_normalization() -> None:
    route = next(route for route in http_routes.ALL_ROUTES if route.path == "/api/auto-approve")
    handler, writes = _capturing_handler(route)
    failure = b'{"ok":false,"status":503,"error":"refreshing"}'
    handler.server.app.auto_approve_status_bytes = lambda session: (
        failure,
        HTTPStatus.SERVICE_UNAVAILABLE,
    )

    http_routes.get_auto_approve(handler, SimpleNamespace(query="session=1"), route)

    payload, status = writes[0]
    assert status == HTTPStatus.ACCEPTED
    assert payload == {
        "state": "queued",
        "request": payload["request"],
        "status": "pending",
        "retry_after_seconds": 1,
        "reason": "upstream service is refreshing",
        "ok": True,
        "terminal": False,
    }
    assert payload["request"]["id"].startswith("r-")


def test_warm_filesystem_response_writes_canonical_bytes_without_a_qualified_promise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route = http_routes.Route(
        "GET",
        "/api/fs/diff",
        "readonly",
        lambda *_args: None,
        protocol=http_routes.RESPONSE_JSON,
    )
    handler, writes = _capturing_handler(route)
    body = b'{"path":"/repo/file.txt","diff":"stable"}'
    product = {
        "format": "json",
        "content_type": "application/json; charset=utf-8",
        "length": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
        "disposition": "inline",
        "filename": "",
    }

    class WarmFilesystemJob:
        def produce(self, _task, _payload, **_kwargs):
            return {"ok": True, "state": "ready", "product": product}, body

    webapp = app_module.TmuxWebtermApp([])
    webapp.job_client = WarmFilesystemJob()
    monkeypatch.setattr(webapp, "filesystem_operation_product_generation", lambda: "watchd:epoch-a:7")
    handler.server = SimpleNamespace(app=webapp)
    handler.auth_identity = MethodType(
        lambda _self: SimpleNamespace(role="admin", username="fixture"),
        handler,
    )
    try:
        handler.submit_filesystem_operation(
            "GET /api/fs/diff",
            "diff",
            "/repo/file.txt",
            {"from_ref": "HEAD", "to_ref": "current"},
        )
        diagnostics = webapp.queued_delivery_ledger.diagnostics()
        operations = webapp.queued_delivery_ledger.open_operations()
    finally:
        webapp.stop_batchd_operation_service()
        webapp.control_server.stop()

    assert writes == [({
        "state": "ready",
        "request": {"id": writes[0][0]["request"]["id"]},
        "data": {"path": "/repo/file.txt", "diff": "stable"},
        "ok": True,
        "terminal": True,
        "path": "/repo/file.txt",
        "diff": "stable",
    }, HTTPStatus.OK)]
    assert diagnostics["queued_delivery_frames"] == []
    assert diagnostics["outstanding_queued"] == []
    assert operations == []


def test_response_parent_rejects_mismatched_product_length_before_ledger_or_write() -> None:
    route = http_routes.Route(
        "GET",
        "/api/fixture",
        "readonly",
        lambda *_args: None,
        protocol=http_routes.RESPONSE_JSON,
    )
    handler = server.Handler.__new__(server.Handler)
    handler._route_response = route
    handler._route_response_written = False
    handler._api_request_id = "r-product-length"
    handler.headers = {}
    product = b'{"sessions":{}}'
    metadata = {
        "format": "json",
        "content_type": "application/json; charset=utf-8",
        "length": len(product) + 1,
        "sha256": hashlib.sha256(product).hexdigest(),
        "disposition": "inline",
        "filename": "",
    }
    handler.server = SimpleNamespace(
        app=SimpleNamespace(
            observe_http_product_delivery=lambda *_args: pytest.fail("ledger must not observe invalid bytes"),
        ),
    )
    handler._write_json_representation = lambda *_args, **_kwargs: pytest.fail("invalid bytes must not be written")

    with pytest.raises(ValueError, match="length does not match"):
        handler.write_product_bytes(product, metadata, promise=("activity-summary", 7))

    assert handler._route_response_written is False


def test_response_parent_forwards_non_json_product_without_framing() -> None:
    route = http_routes.Route(
        "GET",
        "/api/fixture",
        "readonly",
        lambda *_args: None,
        protocol=http_routes.RESPONSE_BINARY,
    )
    handler = server.Handler.__new__(server.Handler)
    handler._route_response = route
    handler._route_response_written = False
    handler._api_request_id = "r-opaque-download"
    handler.headers = {}
    body = b"\x00opaque\xff"
    metadata = {
        "format": "opaque_bytes",
        "content_type": "application/octet-stream",
        "length": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
        "disposition": "attachment",
        "filename": "report.bin",
    }
    writes = []

    def capture(_self, data: bytes, **kwargs) -> None:
        writes.append((data, kwargs))

    handler._write_product_representation = MethodType(capture, handler)

    handler.write_product_bytes(body, metadata)

    assert writes == [(body, {
        "status": HTTPStatus.OK,
        "content_type": "application/octet-stream",
        "disposition": "attachment",
        "filename": "report.bin",
        "product_metadata": metadata,
    })]
    assert handler._route_response_written is True


@pytest.mark.parametrize("disconnect", (False, True), ids=("complete", "disconnect"))
def test_response_parent_streams_artifact_chunks_and_always_closes_lease(disconnect: bool) -> None:
    body = b"first" if disconnect else b"first" + b"second"
    product = {
        "format": "opaque_bytes",
        "content_type": "application/octet-stream" if disconnect else "image/png",
        "length": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
        "disposition": "inline",
        "filename": "",
    }

    class Transfer:
        def __init__(self):
            self.closed = False
            self.product = product

        def read(self, offset):
            return b"first" if offset == 0 else b"second"

        def close(self):
            self.closed = True

    class BrokenWriter:
        def write(self, _body):
            raise BrokenPipeError(errno.EPIPE, "client disconnected")

    transfer = Transfer()
    handler = server.Handler.__new__(server.Handler)
    handler._route_response = http_routes.Route("GET", "/api/fs/raw", "readonly", lambda *_args: None, protocol=http_routes.RESPONSE_BINARY)
    handler._route_response_written = False
    handler.command = "GET"
    handler.wfile = BrokenWriter() if disconnect else io.BytesIO()
    handler.send_response = lambda *_args: None
    handler.send_header = lambda *_args: None
    handler.send_auth_cookie_if_needed = lambda: None
    handler.end_headers = lambda: None
    handler.record_http_response_bytes = lambda *_args, **_kwargs: None

    expectation = pytest.raises(BrokenPipeError) if disconnect else nullcontext()
    with expectation:
        handler.api_response_writer.write_product_stream(transfer)
    assert transfer.closed is True
    if not disconnect:
        assert handler.wfile.getvalue() == body
        assert handler._route_response_written is True


def test_queued_delivery_ledger_registers_explicit_ready_product_terminal_once() -> None:
    missing = QueuedDeliveryLedger()
    with pytest.raises(ValueError, match="was not registered as outstanding"):
        missing.observe_ready_product("activity-summary", 7)

    ledger = QueuedDeliveryLedger()
    ledger.observe_http_response(
        {"status": "queued", "key": "activity-summary", "epoch": 7},
        HTTPStatus.ACCEPTED,
    )

    ledger.observe_ready_product("activity-summary", 7)

    diagnostics = ledger.diagnostics()
    assert diagnostics["outstanding_queued"] == []
    assert diagnostics["queued_delivery_frames"] == [
        {"stream": "activity-summary", "epoch": 7, "seq": 0, "state": "open"},
        {"stream": "activity-summary", "epoch": 7, "seq": 1, "state": "done"},
    ]
    with pytest.raises(ValueError, match="was not registered as outstanding"):
        ledger.observe_ready_product("activity-summary", 7)


def _accepted_delivery_app(ledger: QueuedDeliveryLedger) -> SimpleNamespace:
    """Wire whichever delivery hooks this build exposes to the real ledger.

    The committed/receipt split routes the server's pre-flush and post-flush hooks separately;
    the earlier combined build reached the ledger through observe_http_delivery before the flush.
    Binding whichever this build offers lets one regression reproduce the dishonest receipt claim
    before the split and the honest committed-but-undelivered state after it.
    """

    app = SimpleNamespace()
    if hasattr(ledger, "observe_http_commit"):
        app.observe_http_commit = ledger.observe_http_commit
        app.observe_http_receipt = ledger.observe_http_receipt
    else:
        app.observe_http_delivery = ledger.observe_http_response
    return app


def test_accepted_receipt_is_not_claimed_exposed_when_the_flush_fails() -> None:
    """A failed flush must not persist that the accepted receipt reached the client.

    Invariant under test: the committed/outstanding registration is honest before the flush (the
    causal-visibility race fix), but receipt_exposed reflects the actual client write. When
    _write_json_representation raises BrokenPipe/OSError after the commit, the ticket must stay
    visible as outstanding_queued while receipt_exposed stays False, and the write error must
    propagate rather than be swallowed.
    """

    ledger = QueuedDeliveryLedger()
    receipt = ledger.accept_operation(
        request_id="r-broken-pipe",
        route="GET /api/fs/read",
        deadline_at=0.0,
        progress={"phase": "waiting_for_product"},
        producer={"service": "batchd", "job_id": "job-broken-pipe"},
    )
    operation_id = receipt["operation"]["id"]

    handler = server.Handler.__new__(server.Handler)
    handler._route_response = None
    handler._route_response_written = False
    handler._api_request_id = ""
    handler.headers = {}
    handler.server = SimpleNamespace(app=_accepted_delivery_app(ledger))

    def broken_flush(_self, data, status=HTTPStatus.OK, *, json_encode_ms=0.0, product_metadata=None) -> None:
        del data, status, json_encode_ms, product_metadata
        raise BrokenPipeError(errno.EPIPE, "client hung up before the receipt landed")

    handler._write_json_representation = MethodType(broken_flush, handler)

    accepted_response = {
        "status": "queued",
        "key": "fs-read-stream",
        "epoch": 3,
        "operation": {"id": operation_id},
    }

    with pytest.raises(BrokenPipeError):
        handler.write_api_response(accepted_response, HTTPStatus.ACCEPTED)

    # The race fix holds: the ticket is committed and visible as outstanding server-side state.
    outstanding = ledger.diagnostics()["outstanding_queued"]
    assert len(outstanding) == 1, outstanding
    assert outstanding[0]["key"] == "fs-read-stream"
    assert outstanding[0]["epoch"] == 3

    # But the failed write must not claim the accepted receipt reached the client.
    assert ledger._operations[operation_id]["receipt_exposed"] is False


def test_response_parent_preserves_an_existing_canonical_result() -> None:
    route = http_routes.Route(
        "GET",
        "/api/fixture",
        "readonly",
        lambda *_args: None,
        protocol=http_routes.RESPONSE_JSON,
    )
    handler, writes = _capturing_handler(route)
    canonical = {
        "state": "ready",
        "request": {"id": "r-canonical-fixture"},
        "data": {"answer": 42},
        "quality": {"complete": True, "stale": False},
    }

    handler.write_json(canonical)

    payload, status = writes[0]
    assert status == HTTPStatus.OK
    assert payload == canonical


def test_response_parent_keeps_legacy_queued_identity_aliases() -> None:
    route = http_routes.Route(
        "POST",
        "/api/fixture",
        "admin",
        lambda *_args: None,
        protocol=http_routes.RESPONSE_JSON,
    )
    handler, writes = _capturing_handler(route)

    handler.write_json(
        {
            "ok": True,
            "status": "queued",
            "key": "fixture-refresh",
            "ticket": "fixture-refresh-7",
            "epoch": 7,
        },
        status=HTTPStatus.ACCEPTED,
    )

    payload, status = writes[0]
    assert status == HTTPStatus.ACCEPTED
    assert payload["state"] == "queued"
    assert payload["operation"]["id"] == "fixture-refresh"
    assert payload["status"] == "queued"
    assert payload["key"] == "fixture-refresh"
    assert payload["ticket"] == "fixture-refresh-7"
    assert payload["epoch"] == 7


def test_response_parent_preserves_bounded_read_pending_without_operation_identity() -> None:
    route = http_routes.Route(
        "GET",
        "/api/stats-snapshot",
        "readonly",
        lambda *_args: None,
        protocol=http_routes.RESPONSE_JSON,
    )
    handler, writes = _capturing_handler(route)

    handler.write_json(
        {
            "status": "pending",
            "retry_after_seconds": 1,
            "reason": "statsd is refreshing",
        },
        status=HTTPStatus.ACCEPTED,
    )

    payload, status = writes[0]
    assert status == HTTPStatus.ACCEPTED
    assert payload["state"] == "queued"
    assert payload["status"] == "pending"
    assert payload["retry_after_seconds"] == 1
    assert payload["reason"] == "statsd is refreshing"
    assert "operation" not in payload
    assert payload["ok"] is True and payload["terminal"] is False


@pytest.mark.parametrize(
    "failure",
    (
        {"ok": False, "_transport_error": "timeout", "error": "timed out"},
        {"ok": False, "_transport_error": "absent", "error": "socket absent"},
        {"ok": False, "_transport_error": "refused", "error": "connection refused"},
        {"ok": False, "_transport_error": "rpc", "error": "response exceeded deadline"},
        {"ok": False, "status": HTTPStatus.SERVICE_UNAVAILABLE, "error": "service busy"},
        {"status": "unavailable", "reason": "upstream detail was already normalized"},
    ),
)
def test_response_parent_maps_every_transient_read_failure_to_bounded_pending(failure) -> None:
    route = http_routes.Route(
        "GET",
        "/api/fixture",
        "readonly",
        lambda *_args: None,
        protocol=http_routes.RESPONSE_JSON,
    )
    handler, writes = _capturing_handler(route)

    handler.write_json(failure, status=HTTPStatus.FAILED_DEPENDENCY)

    payload, status = writes[0]
    assert status == HTTPStatus.ACCEPTED
    assert payload == {
        "state": "queued",
        "request": payload["request"],
        "status": "pending",
        "retry_after_seconds": 1,
        "reason": "upstream service is refreshing",
        "ok": True,
        "terminal": False,
    }
    assert payload["request"]["id"].startswith("r-")


@pytest.mark.parametrize(
    ("method", "failure"),
    (
        ("GET", {"ok": False, "terminal": True, "status": "unavailable", "error": "migration failed"}),
        ("POST", {"ok": False, "_transport_error": "timeout", "error": "timed out"}),
    ),
)
def test_response_parent_keeps_terminal_reads_and_mutations_as_typed_failures(method, failure) -> None:
    route = http_routes.Route(
        method,
        "/api/fixture",
        "readonly",
        lambda *_args: None,
        protocol=http_routes.RESPONSE_JSON,
    )
    handler, writes = _capturing_handler(route)

    handler.write_json(failure, status=HTTPStatus.FAILED_DEPENDENCY)

    payload, status = writes[0]
    assert status == HTTPStatus.FAILED_DEPENDENCY
    assert payload["state"] == "failed"
    assert payload["error"]["code"] == "dependency_failed"


def test_response_parent_propagates_exception_type_frames_and_correlated_log() -> None:
    route = http_routes.Route(
        "GET",
        "/api/fixture",
        "readonly",
        lambda *_args: None,
        protocol=http_routes.RESPONSE_JSON,
    )
    handler, writes = _capturing_handler(route)
    before_sequence = server_logs.SERVER_LOGS.payload()["sequence"]

    def fail() -> None:
        raise ValueError("fixture failure")

    handler.dispatch_route_response(route, fail)

    payload, status = writes[0]
    error = payload["error"]
    assert status == HTTPStatus.INTERNAL_SERVER_ERROR
    assert payload["state"] == "failed"
    assert error["code"] == "internal_error"
    assert error["stack"][0]["exception"] == {"type": "ValueError", "message": "fixture failure"}
    assert error["stack"][0]["frames"][-1]["function"] == "fail"
    new_logs = [entry for entry in server_logs.SERVER_LOGS.payload()["logs"] if entry["id"] > before_sequence]
    log_payload = json.loads(new_logs[-1]["message"])
    assert log_payload == {
        "code": error["code"],
        "operation": None,
        "origin": error["origin"],
        "request": payload["request"],
        "stack": error["stack"],
    }


def test_response_parent_keeps_local_service_failure_cause_in_api_envelope(monkeypatch, tmp_path) -> None:
    route = http_routes.Route(
        "POST",
        "/api/fixture",
        "readonly",
        lambda *_args: None,
        protocol=http_routes.RESPONSE_JSON,
    )
    handler, writes = _capturing_handler(route)
    socket_path = Path(tmp_path) / "missing.sock"
    client = LocalServiceClient("fixture", "fixture.module", socket_path, service_dir=socket_path.parent)

    def fail_request(*_args, **_kwargs):
        raise FileNotFoundError(errno.ENOENT, "fixture socket absent")

    monkeypatch.setattr(local_service_client_module, "local_service_request", fail_request)
    monkeypatch.setattr(client.registry, "ensure_started", lambda: False)
    failure = client.request({"action": "status"})
    handler.write_json(failure, status=HTTPStatus.SERVICE_UNAVAILABLE)

    payload, status = writes[0]
    root = payload["error"]["stack"][-1]
    assert status == HTTPStatus.SERVICE_UNAVAILABLE
    assert root["exception"] == failure["cause"]["exception"] == {
        "type": "FileNotFoundError",
        "message": "[Errno 2] fixture socket absent",
    }
    assert root["frames"] == failure["cause"]["frames"]
    assert root["frames"]


def test_local_service_transport_log_names_action_request_and_client_elapsed_time(monkeypatch, tmp_path) -> None:
    socket_path = Path(tmp_path) / "watchd.sock"
    client = LocalServiceClient("watchd", "fixture.module", socket_path, service_dir=socket_path.parent)
    request_ids = []
    emitted = []
    clock = iter((10.0, 10.0, 10.125))

    def timeout_request(_socket_path, envelope, **_kwargs):
        request_ids.append(envelope.request_id)
        raise TimeoutError("fixture transport timed out")

    monkeypatch.setattr(local_service_client_module, "monotonic_clock", lambda: next(clock), raising=False)
    monkeypatch.setattr(local_service_client_module, "local_service_request", timeout_request)
    monkeypatch.setattr(local_service_client_module, "emit_server_log", lambda *args, **kwargs: emitted.append((args, kwargs)))

    response = client.request({"action": "wait_revision"}, timeout=0.2)

    assert response["_transport_error"] == "timeout"
    assert len(request_ids) == len(emitted) == 1
    args, kwargs = emitted[0]
    assert args[:2] == ("error", "local-service:watchd")
    assert f"action=wait_revision request_id={request_ids[0]} client_elapsed_ms=125.000" in args[2]
    assert kwargs["dedupe_key"] == "local-service:watchd:wait_revision:TimeoutError:timeout"
    assert {
        "request_id": kwargs["request_id"],
        "route": kwargs["route"],
        "event": kwargs["event"],
        "delivery": kwargs["delivery"],
    } == {
        "request_id": request_ids[0],
        "route": "local-service:watchd",
        "event": "wait_revision",
        "delivery": "timeout",
    }


def test_non_json_route_exception_stays_with_its_declared_protocol() -> None:
    route = http_routes.Route(
        "GET",
        "/api/fixture.bin",
        "readonly",
        lambda *_args: None,
        protocol=http_routes.RESPONSE_BINARY,
    )
    handler, writes = _capturing_handler(route)

    def fail() -> None:
        raise ValueError("binary fixture failure")

    with pytest.raises(ValueError, match="binary fixture failure"):
        handler.dispatch_route_response(route, fail)

    assert writes == []


def test_response_parent_preserves_an_empty_not_modified_result() -> None:
    route = http_routes.Route(
        "GET",
        "/api/fixture",
        "readonly",
        lambda *_args: None,
        protocol=http_routes.RESPONSE_JSON,
    )
    handler, writes = _capturing_handler(route)
    bodyless: list[HTTPStatus] = []

    def capture(_self, status: HTTPStatus) -> None:
        bodyless.append(status)

    handler._write_bodyless_api_response = MethodType(capture, handler)

    handler.write_json_bytes(b"", status=HTTPStatus.NOT_MODIFIED)

    assert bodyless == [HTTPStatus.NOT_MODIFIED]
    assert writes == []


def test_response_parent_rejects_a_contradictory_canonical_result() -> None:
    route = http_routes.Route(
        "GET",
        "/api/fixture",
        "readonly",
        lambda *_args: None,
        protocol=http_routes.RESPONSE_JSON,
    )
    handler, writes = _capturing_handler(route)

    with pytest.raises(ValueError, match="ready API response"):
        handler.write_json({
            "state": "ready",
            "request": {"id": "r-invalid-fixture"},
            "error": {"code": "not_ready"},
        })

    assert writes == []
