# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Registered-route contract for a filesystem operation the web thread can already refuse.

`tests/test_app.py::test_filesystem_operation_refuses_an_invalid_path_before_accepting_it` calls
`filesystem_operation_http_payload()` directly.  That stops one component short of the wire: it
never runs the route registry, `Handler.submit_filesystem_operation`, `write_json`, or
`write_api_response`.  The response parent is where this morning's watch-diff 400 turned into a
browser 500, and it is also the component that records a failed API response as an operator log
row -- so a direct call cannot substantiate a claim about either the status on the wire or the log
rows the request produces.  These tests drive `POST /api/fs/mkdir` through real dispatch.
"""

from __future__ import annotations

import json
import re
from http import HTTPStatus

import pytest

from yolomux_lib import app as app_module
from yolomux_lib import server_logs

from tests.helpers.http_routes import capturing_route_request as _capturing_route_request


class _RefusingFilesystemJob:
    """Record any jobd traffic a refused request should never have produced."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def produce(self, task, payload, **kwargs):
        self.calls.append(("produce", task, payload, kwargs))
        raise AssertionError("a refused filesystem request must not reach jobd")

    def product(self, product_key, timeout=0.5):
        self.calls.append(("product", product_key))
        raise AssertionError("a refused filesystem request must not read a product")

    def result(self, job_id):
        self.calls.append(("result", job_id))
        raise AssertionError("a refused filesystem request must not poll a job")


def test_fs_mkdir_route_refuses_an_empty_path_on_the_wire(monkeypatch, tmp_path):
    """`POST /api/fs/mkdir {}` answers a typed 400 through the real writer, holding nothing."""

    monkeypatch.setattr(app_module, "SESSION_FILES_OPERATION_STATE_PATH", tmp_path / "operations.json")
    webapp = app_module.TmuxWebtermApp([])
    webapp.job_client = _RefusingFilesystemJob()
    dispatch, writes = _capturing_route_request(webapp, "/api/fs/mkdir", method="POST", body=b"{}")
    server_logs.SERVER_LOGS.clear()
    try:
        dispatch()
        log_entries = server_logs.SERVER_LOGS.payload()["logs"]
    finally:
        webapp.stop_jobd_operation_service()
        webapp.control_server.stop()

    assert len(writes) == 1, writes
    payload, status = writes[0]

    # 1. The status the browser actually receives, from the real response writer.
    assert status == HTTPStatus.BAD_REQUEST, payload

    # 2. A valid typed envelope -- the shape `write_api_response` validates and would have raised on.
    assert payload["state"] == "failed"
    assert payload["ok"] is False
    assert payload["terminal"] is True
    assert payload["status"] == int(HTTPStatus.BAD_REQUEST)
    assert "data" not in payload and "operation" not in payload
    error = payload["error"]
    assert error["code"] == "invalid_request"
    assert error["retryable"] is False
    assert error["message"]["key"] == "fs.error.pathRequired"
    assert error["stack"] == [{
        "component": "server.http",
        "operation": "POST /api/fs/mkdir",
        "code": "invalid_request",
    }]
    request_id = payload["request"]["id"]
    assert re.fullmatch(r"r-[A-Za-z0-9._-]{1,120}", request_id), payload

    # 3. Nothing was submitted, reserved or retained: no jobd call, no receipt, no future, no slot.
    assert webapp.job_client.calls == []
    assert webapp.queued_delivery_ledger.open_operations() == []
    assert webapp.jobd_operation_service.futures == set()
    assert not (tmp_path / "operations.json").exists(), "a refused request must not persist a receipt"

    # 4. The correlated log behaviour the direct call could not observe.  `write_api_response`
    #    records every failed API response, so the row exists -- and it must name THIS request and
    #    THIS route, so an operator reading it can attribute the failure instead of finding an
    #    orphan `invalid_request` terminalized out of band by a worker.
    failures = [entry for entry in log_entries if entry["level"] in {"warning", "error"}]
    assert len(failures) == 1, failures
    record = failures[0]
    assert record["source"] == "api-response"
    assert record["category"] == "api"
    correlated = json.loads(record["message"])
    assert correlated["request"] == {"id": request_id}
    assert correlated["operation"] is None, "a refused request never owns an operation"
    assert correlated["code"] == "invalid_request"
    assert correlated["stack"] == [{
        "component": "server.http",
        "operation": "POST /api/fs/mkdir",
        "code": "invalid_request",
    }]


@pytest.mark.parametrize(
    ("path", "message_key"),
    (
        ("relative/note.txt", "fs.error.pathAbsolute"),
        ("/repo/bad\nname", "fs.error.pathIllegal"),
    ),
)
def test_fs_mkdir_route_refuses_every_lexical_shape_with_the_same_typed_400(monkeypatch, tmp_path, path, message_key):
    """The other lexical refusals reach the wire as the same envelope, not as a 202 or a 500."""

    monkeypatch.setattr(app_module, "SESSION_FILES_OPERATION_STATE_PATH", tmp_path / "operations.json")
    webapp = app_module.TmuxWebtermApp([])
    webapp.job_client = _RefusingFilesystemJob()
    body = json.dumps({"path": path}).encode("utf-8")
    dispatch, writes = _capturing_route_request(webapp, "/api/fs/mkdir", method="POST", body=body)
    try:
        dispatch()
    finally:
        webapp.stop_jobd_operation_service()
        webapp.control_server.stop()

    payload, status = writes[0]
    assert status == HTTPStatus.BAD_REQUEST, payload
    assert payload["state"] == "failed"
    assert payload["error"]["code"] == "invalid_request"
    assert payload["error"]["message"]["key"] == message_key
    assert payload["error"]["stack"][0]["operation"] == "POST /api/fs/mkdir"
    assert webapp.job_client.calls == []
    assert webapp.queued_delivery_ledger.open_operations() == []
    assert webapp.jobd_operation_service.futures == set()
