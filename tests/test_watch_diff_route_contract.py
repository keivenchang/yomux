# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Route-level contract for `GET /api/fs/watch-diff` and `POST /api/fs/batch`.

These tests drive the registered route through `dispatch_route_response()` and the real
response writer, because the live HTTP 500 only appears once the canonical failure envelope
reaches `write_api_response()`.  A direct call to the app function never sees it.
"""

from __future__ import annotations

import json
import re
import threading
import time
from http import HTTPStatus
from types import SimpleNamespace

import pytest

from tests.helpers.http_routes import capturing_route_request as _capturing_route_request

from yolomux_lib import app as app_module
from yolomux_lib import common
from yolomux_lib import http_routes
from yolomux_lib import server


class _StoppedCompletionService(app_module.JobdOperationService):
    """Accept the reservation and retain the submitted worker without running it."""

    def __init__(self) -> None:
        super().__init__()
        self.submissions: list[tuple] = []

    def submit_reserved(self, reservation, function, *args) -> bool:
        assert isinstance(reservation, app_module.JobdOperationReservation)
        self.submissions.append((function, args))
        return True

    def stop(self) -> None:
        self.stop_event.set()


def _watch_diff_app(monkeypatch, tmp_path, roots: list[str]):
    monkeypatch.setattr(app_module, "SESSION_FILES_OPERATION_STATE_PATH", tmp_path / "operations.json")
    webapp = app_module.TmuxWebtermApp([])
    webapp.jobd_operation_service = _StoppedCompletionService()
    monkeypatch.setattr(webapp, "client_watch_roots_snapshot", lambda: list(roots))
    monkeypatch.setattr(webapp, "publish_client_event", lambda *_args, **_kwargs: None)
    return webapp


@pytest.mark.parametrize("root_count", (1, 64, 65, 128))
def test_watch_diff_route_accepts_every_root_count_the_client_index_admits(monkeypatch, tmp_path, root_count):
    """The client index admits 128 roots, so every accepted count must produce a receipt, not a 500."""

    roots = [f"/repo-{index:03d}" for index in range(root_count)]
    webapp = _watch_diff_app(monkeypatch, tmp_path, roots)
    dispatch, writes = _capturing_route_request(webapp, "/api/fs/watch-diff?full=1")
    try:
        dispatch()
    finally:
        webapp.stop_jobd_operation_service()
        webapp.control_server.stop()

    payload, status = writes[0]
    expected_batches = -(-root_count // app_module.filesystem.MAX_BATCH_REQUESTS)
    assert status == HTTPStatus.ACCEPTED, payload
    assert payload["state"] == "queued"
    assert payload["operation"]["context"]["roots"] == root_count
    assert payload["operation"]["context"]["batches"] == expected_batches
    assert payload["operation"]["progress"]["batches_total"] == expected_batches
    function, args = webapp.jobd_operation_service.submissions[0]
    assert function == webapp.complete_filesystem_watch_diff_operation
    assert args[2] == roots


def test_watch_diff_route_reports_no_roots_as_a_ready_empty_plan(monkeypatch, tmp_path):
    webapp = _watch_diff_app(monkeypatch, tmp_path, [])
    dispatch, writes = _capturing_route_request(webapp, "/api/fs/watch-diff?full=1")
    try:
        dispatch()
    finally:
        webapp.stop_jobd_operation_service()
        webapp.control_server.stop()

    payload, status = writes[0]
    assert status == HTTPStatus.OK, payload
    assert payload["state"] == "ready"
    assert payload["data"]["mode"] == "full"
    assert payload["data"]["reason"] == "forced"
    assert payload["data"]["removed_roots"] == []
    assert webapp.jobd_operation_service.submissions == []


def test_watch_diff_route_rejects_more_roots_than_the_client_index_admits(monkeypatch, tmp_path):
    """Above the 128-root client contract the answer is a typed 400, never an internal 500."""

    roots = [f"/repo-{index:03d}" for index in range(app_module.CLIENT_WATCH_ROOT_LIMIT + 1)]
    webapp = _watch_diff_app(monkeypatch, tmp_path, roots)
    dispatch, writes = _capturing_route_request(webapp, "/api/fs/watch-diff?full=1")
    try:
        dispatch()
    finally:
        webapp.stop_jobd_operation_service()
        webapp.control_server.stop()

    payload, status = writes[0]
    assert status == HTTPStatus.BAD_REQUEST, payload
    assert payload["state"] == "failed"
    assert payload["error"]["code"] == "invalid_request"
    assert payload["error"]["details"] == {
        "roots": len(roots),
        "maximum": app_module.CLIENT_WATCH_ROOT_LIMIT,
    }
    assert payload["error"]["stack"] == [{
        "component": "server.http",
        "operation": "GET /api/fs/watch-diff",
        "code": "invalid_request",
    }]
    assert webapp.jobd_operation_service.submissions == []


@pytest.mark.parametrize(
    ("requests", "message_key", "details"),
    (
        ("not-a-list", "request.error.list", {"requests_type": "str"}),
        (
            [{"id": index, "type": "list", "path": f"/repo-{index}"} for index in range(65)],
            "request.error.tooManyItems",
            {"requests": 65, "maximum": 64},
        ),
    ),
    ids=("not-a-list", "over-the-batch-limit"),
)
def test_fs_batch_route_returns_a_typed_400_for_an_invalid_request_list(monkeypatch, tmp_path, requests, message_key, details):
    """`POST /api/fs/batch` shares the missing-stack defect and must not answer 500 either."""

    monkeypatch.setattr(app_module, "SESSION_FILES_OPERATION_STATE_PATH", tmp_path / "operations.json")
    webapp = app_module.TmuxWebtermApp([])
    body = json.dumps({"requests": requests}).encode("utf-8")
    dispatch, writes = _capturing_route_request(webapp, "/api/fs/batch", method="POST", body=body)
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
    assert payload["error"]["details"] == details
    assert payload["error"]["stack"] == [{
        "component": "server.http",
        "operation": "POST /api/fs/batch",
        "code": "invalid_request",
    }]


def test_canonical_error_payload_rejects_a_missing_or_incomplete_causal_stack():
    """A caller mistake fails in the producer, before it can become a browser HTTP 500."""

    with pytest.raises(ValueError, match="canonical failure payload requires a causal stack"):
        common.error_payload(
            "no stack",
            message_key="request.error.list",
            canonical=True,
            code="invalid_request",
            origin="server.http",
        )
    with pytest.raises(ValueError, match="canonical failure payload requires a causal stack"):
        common.error_payload(
            "empty stack",
            message_key="request.error.list",
            canonical=True,
            code="invalid_request",
            origin="server.http",
            stack=[],
        )
    for frame in ({"operation": "GET /x", "code": "c"}, {"component": "server.http", "code": "c"}, {"component": "server.http", "operation": "GET /x"}):
        with pytest.raises(ValueError, match="canonical failure payload requires a causal stack"):
            common.error_payload(
                "incomplete frame",
                message_key="request.error.list",
                canonical=True,
                code="invalid_request",
                origin="server.http",
                stack=[frame],
            )
    payload = common.error_payload(
        "complete frame",
        message_key="request.error.list",
        canonical=True,
        code="invalid_request",
        origin="server.http",
        stack=[{"component": "server.http", "operation": "GET /x", "code": "invalid_request"}],
    )
    assert payload["error"]["stack"] == [{
        "component": "server.http",
        "operation": "GET /x",
        "code": "invalid_request",
    }]


def test_every_canonical_failure_caller_supplies_its_own_complete_frame():
    """No caller may rely on a guessed frame: every `canonical=True` site names its own operation."""

    sources = {
        name: (app_module.Path(app_module.__file__).parent / relative).read_text(encoding="utf-8")
        for name, relative in (("app", "app.py"), ("server", "server.py"), ("common", "infra/common.py"))
    }
    call_sites = sum(
        len(re.findall(r"^\s*canonical=True,\s*$", text, flags=re.MULTILINE))
        for text in sources.values()
    )
    assert call_sites == 9, f"canonical=True call-site count changed to {call_sites}; audit every new site"
    # The construction owner is the only place a stack may be defaulted, and it defaults to a raise.
    assert "validated_causal_stack(stack)" in sources["common"]
    assert "def validated_causal_stack" in sources["common"]


class _RecordingBatchJob:
    """Accept every submitted chunk and hand back a queued receipt for it."""

    def __init__(self) -> None:
        self.submissions: list[tuple[str, dict, dict]] = []

    def produce(self, task, payload, **kwargs):
        self.submissions.append((task, payload, kwargs))
        index = len(self.submissions)
        return {
            "ok": True,
            "state": "queued",
            "job": {"job_id": f"job-{index}", "status": "queued", "generation": kwargs["generation"]},
            "product": {"coalesce_key": kwargs["coalesce_key"], "generation": 0},
        }, b""


@pytest.mark.parametrize("root_count", (0, 1, 64, 65, 128))
def test_watch_batch_partition_covers_every_root_exactly_once(monkeypatch, tmp_path, root_count):
    roots = [f"/repo-{index:03d}" for index in range(root_count)]
    webapp = _watch_diff_app(monkeypatch, tmp_path, roots)
    webapp.job_client = _RecordingBatchJob()
    try:
        batches = webapp.submit_filesystem_watch_batches(roots, "seed")
    finally:
        webapp.stop_jobd_operation_service()
        webapp.control_server.stop()

    limit = app_module.filesystem.MAX_BATCH_REQUESTS
    submitted_paths = [
        [str(request["path"]) for request in payload["requests"]]
        for _task, payload, _kwargs in webapp.job_client.submissions
    ]
    expected_sizes = [
        min(limit, root_count - start)
        for start in range(0, root_count, limit)
    ]
    assert len(batches) == len(expected_sizes) == -(-root_count // limit)
    assert [len(chunk) for chunk in submitted_paths] == expected_sizes
    assert all(len(chunk) <= limit for chunk in submitted_paths)
    assert [path for chunk in submitted_paths for path in chunk] == roots, "no root may be dropped or duplicated"
    assert [batch.root_offset for batch in batches] == [index * limit for index in range(len(batches))]
    assert [batch.root_count for batch in batches] == [len(chunk) for chunk in submitted_paths]
    assert len({batch.producer.product_key for batch in batches}) == len(batches), "chunks need distinct product keys"


def test_watch_batch_partition_keeps_repeated_roots_on_their_own_index(monkeypatch, tmp_path):
    """A duplicate root is still one request slot; merging must not fold two slots together."""

    roots = ["/repo-a", "/repo-b", "/repo-a"]
    webapp = _watch_diff_app(monkeypatch, tmp_path, roots)
    webapp.job_client = _RecordingBatchJob()
    try:
        batches = webapp.submit_filesystem_watch_batches(roots, "seed")
        products = [{
            "responses": [
                {"id": 0, "ok": True, "status": 200, "payload": {"entries": []}},
                {"id": 1, "ok": False, "status": 503, "error": "b failed"},
                {"id": 2, "ok": True, "status": 200, "payload": {"entries": [{"kind": "file"}]}},
            ],
        }]
        merged = webapp.filesystem_watch_payload_from_products({"mode": "full"}, roots, products)
    finally:
        webapp.stop_jobd_operation_service()
        webapp.control_server.stop()

    assert len(batches) == 1
    assert merged["roots"] == roots
    assert [directory["path"] for directory in merged["directories"]] == roots
    assert [directory["ok"] for directory in merged["directories"]] == [True, False, True]
    assert merged["listing_summary"]["roots_requested"] == 3


def _pending_batches(count: int) -> tuple:
    limit = app_module.filesystem.MAX_BATCH_REQUESTS
    return tuple(
        app_module.FilesystemWatchBatchProduct(
            producer=app_module.JobdProductOperation(
                job_id=f"job-{index}",
                product_key=f"fs-watch:chunk-{index}",
                generation=1,
            ),
            root_offset=index * limit,
            root_count=limit,
        )
        for index in range(count)
    )


def _chunk_product(offset: int, count: int, *, failed_ids: frozenset = frozenset()) -> dict:
    return {
        "responses": [
            {"id": index, "ok": False, "status": 503, "error": f"root {offset + index} failed"}
            if index in failed_ids
            else {"id": index, "ok": True, "status": 200, "payload": {"entries": []}}
            for index in range(count)
        ],
    }


def test_out_of_order_child_completion_still_merges_in_root_order(monkeypatch, tmp_path):
    """The last chunk finishes first; the merged payload is still parent root order.

    jobd numbers every batch's responses from zero, so both children arrive claiming ids
    ``0..63``.  Without the per-chunk offset the later chunk would overwrite the earlier one and
    half the roots would silently report "filesystem batch result missing".
    """

    limit = app_module.filesystem.MAX_BATCH_REQUESTS
    roots = [f"/repo-{index:03d}" for index in range(2 * limit)]
    webapp = _watch_diff_app(monkeypatch, tmp_path, roots)
    # The second child completed before submission of the first one was even resolved: it comes
    # back warm, while the first child is still cold and has to be waited for.
    batches = (
        app_module.FilesystemWatchBatchProduct(
            producer=app_module.JobdProductOperation(job_id="job-0", product_key="fs-watch:c0", generation=1),
            root_offset=0,
            root_count=limit,
        ),
        app_module.FilesystemWatchBatchProduct(
            producer=app_module.JobdProductOperation(job_id="job-1", product_key="fs-watch:c1", generation=1),
            ready_product=_chunk_product(limit, limit),
            root_offset=limit,
            root_count=limit,
        ),
    )
    waited: list[str] = []

    def wait_for_product(producer, _deadline_at, *, cancel_event=None):
        del cancel_event
        waited.append(producer.job_id)
        return _chunk_product(0, limit)

    monkeypatch.setattr(webapp, "wait_for_jobd_operation_product", wait_for_product)
    try:
        products = webapp.resolve_filesystem_watch_batches(batches, 0.0)
        merged = webapp.filesystem_watch_payload_from_products({"mode": "full"}, roots, products)
    finally:
        webapp.stop_jobd_operation_service()
        webapp.control_server.stop()

    assert waited == ["job-0"], "the already-complete child must not be waited for again"
    assert [response["id"] for product in products for response in product["responses"]] == list(range(2 * limit))
    assert [directory["path"] for directory in merged["directories"]] == roots
    assert merged["listing_summary"]["roots_listed"] == 2 * limit
    assert merged["listing_summary"]["roots_error"] == 0


def test_one_failed_child_batch_keeps_its_own_roots_failed_and_the_rest_listed(monkeypatch, tmp_path):
    """A per-root failure in one chunk must survive the merge instead of failing the request."""

    limit = app_module.filesystem.MAX_BATCH_REQUESTS
    roots = [f"/repo-{index:03d}" for index in range(limit + 3)]
    webapp = _watch_diff_app(monkeypatch, tmp_path, roots)
    batches = (
        app_module.FilesystemWatchBatchProduct(
            producer=app_module.JobdProductOperation(job_id="job-0", product_key="fs-watch:c0", generation=1),
            ready_product=_chunk_product(0, limit),
            root_offset=0,
            root_count=limit,
        ),
        app_module.FilesystemWatchBatchProduct(
            producer=app_module.JobdProductOperation(job_id="job-1", product_key="fs-watch:c1", generation=1),
            ready_product=_chunk_product(limit, 3, failed_ids=frozenset({0, 1, 2})),
            root_offset=limit,
            root_count=3,
        ),
    )
    try:
        products = webapp.resolve_filesystem_watch_batches(batches, 0.0)
        merged = webapp.filesystem_watch_payload_from_products({"mode": "full"}, roots, products)
    finally:
        webapp.stop_jobd_operation_service()
        webapp.control_server.stop()

    assert merged["listing_summary"]["roots_requested"] == limit + 3
    assert merged["listing_summary"]["roots_listed"] == limit
    assert merged["listing_summary"]["roots_error"] == 3
    failed = [directory for directory in merged["directories"] if directory["ok"] is False]
    assert [directory["path"] for directory in failed] == roots[limit:]
    assert [directory["error"] for directory in failed] == [
        f"root {index} failed" for index in range(limit, limit + 3)
    ]


def test_a_failed_child_submission_fails_the_whole_parent_operation(monkeypatch, tmp_path):
    """A chunk jobd refuses is a request failure with a typed cause, never a partial answer."""

    limit = app_module.filesystem.MAX_BATCH_REQUESTS
    roots = [f"/repo-{index:03d}" for index in range(limit + 1)]
    webapp = _watch_diff_app(monkeypatch, tmp_path, roots)
    accepted: list[dict] = []

    class SecondChunkRefusingJob:
        def produce(self, task, payload, **kwargs):
            del task
            if accepted:
                return {"ok": False, "error": "jobd rejected the second watch chunk"}, b""
            accepted.append(payload)
            return {
                "ok": True,
                "state": "queued",
                "job": {"job_id": "job-0", "status": "queued", "generation": kwargs["generation"]},
                "product": {"coalesce_key": kwargs["coalesce_key"], "generation": 0},
            }, b""

    webapp.job_client = SecondChunkRefusingJob()
    try:
        with pytest.raises(app_module.JobdOperationUnavailable, match="jobd rejected the second watch chunk"):
            webapp.submit_filesystem_watch_batches(roots, "seed", delivery="ready_or_receipt")
    finally:
        webapp.stop_jobd_operation_service()
        webapp.control_server.stop()

    assert len(accepted) == 1


def test_watch_diff_request_replays_its_retained_multi_batch_product_without_resubmitting(monkeypatch, tmp_path):
    """The retained product of a partitioned request is keyed by the whole request, not a chunk."""

    limit = app_module.filesystem.MAX_BATCH_REQUESTS
    roots = [f"/repo-{index:03d}" for index in range(limit + 5)]
    webapp = _watch_diff_app(monkeypatch, tmp_path, roots)
    try:
        base_payload = {"mode": "full", "reason": "forced", "token": "", "removed_roots": []}
        identity_seed = webapp.filesystem_watch_batch_identity_seed(base_payload, roots)
        products = [_chunk_product(0, limit), _chunk_product(limit, 5)]
        products[1] = app_module.filesystem_watch_product_at_offset(products[1], limit)
        request_key = app_module.filesystem_watch_request_product_key(roots, identity_seed)
        webapp.materialize_filesystem_watch_products(
            base_payload,
            roots,
            products,
            product_keys={request_key},
        )
        monkeypatch.setattr(
            webapp,
            "submit_filesystem_watch_batches",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("a retained product must not resubmit")),
        )
        payload, status = webapp.filesystem_watch_diff_http_payload(force_full=True, request_id="r-web-retained")
    finally:
        webapp.stop_jobd_operation_service()
        webapp.control_server.stop()

    assert status == HTTPStatus.OK
    assert payload["roots"] == roots
    assert payload["listing_summary"]["roots_listed"] == limit + 5
    assert payload["listing_summary"]["roots_error"] == 0


def test_accepted_watch_diff_threads_one_absolute_deadline_through_every_child(monkeypatch, tmp_path):
    """The deadline is fixed at HTTP acceptance and every child shares it, so queue delay is spent
    against the same wall-clock bound instead of restarting per child."""

    limit = app_module.filesystem.MAX_BATCH_REQUESTS
    roots = [f"/repo-{index:03d}" for index in range(limit + 1)]
    webapp = _watch_diff_app(monkeypatch, tmp_path, roots)
    accepted_at = time.time()
    try:
        receipt, status = webapp.filesystem_watch_diff_http_payload(force_full=True, request_id="r-deadline")
    finally:
        webapp.stop_jobd_operation_service()
        webapp.control_server.stop()

    assert status == HTTPStatus.ACCEPTED, receipt
    _function, args = webapp.jobd_operation_service.submissions[0]
    # args == (flight, base_payload, roots, identity_seed)
    deadline_at = args[0].deadline_at
    # One absolute deadline, set at acceptance -- not a per-child relative timeout.
    assert accepted_at + app_module.FS_BATCH_OPERATION_DEADLINE_SECONDS <= deadline_at <= time.time() + app_module.FS_BATCH_OPERATION_DEADLINE_SECONDS

    # A child dequeued after a queue delay still waits against that same absolute deadline.
    batches = tuple(
        app_module.FilesystemWatchBatchProduct(
            producer=app_module.JobdProductOperation(job_id=f"job-{index}", product_key=f"fs-watch:c{index}", generation=1),
            root_offset=index * limit,
            root_count=min(limit, len(roots) - index * limit),
        )
        for index in range(2)
    )
    observed_deadlines: list[float] = []

    def wait_for_product(producer, child_deadline_at, *, cancel_event=None):
        del producer, cancel_event
        observed_deadlines.append(child_deadline_at)
        return _chunk_product(0, limit)

    monkeypatch.setattr(webapp, "wait_for_jobd_operation_product", wait_for_product)
    webapp.resolve_filesystem_watch_batches(batches, deadline_at)
    assert observed_deadlines == [deadline_at, deadline_at], "every child shares the one acceptance deadline"


def test_cancelled_watch_diff_abandons_the_wait_publishes_nothing_and_leaves_children(monkeypatch, tmp_path):
    """A cancelled receipt fence abandons the parent wait before touching the broker, publishes no
    terminal result or error, and never issues a broker cancellation for the accepted children."""

    limit = app_module.filesystem.MAX_BATCH_REQUESTS
    roots = [f"/repo-{index:03d}" for index in range(limit + 1)]
    webapp = _watch_diff_app(monkeypatch, tmp_path, roots)
    flight = app_module.JobdOperationFlight(
        lane="bulk",
        key="fs-watch-request:cancelled",
        deadline_at=time.time() + app_module.FS_BATCH_OPERATION_DEADLINE_SECONDS,
    )
    flight.cancel_owner()  # a failed acceptance cancelled this parent before it resolved

    cold_batches = tuple(
        app_module.FilesystemWatchBatchProduct(
            producer=app_module.JobdProductOperation(job_id=f"job-{index}", product_key=f"fs-watch:c{index}", generation=1),
            root_offset=index * limit,
            root_count=min(limit, len(roots) - index * limit),
        )
        for index in range(2)
    )
    monkeypatch.setattr(webapp, "submit_filesystem_watch_batches", lambda *_args, **_kwargs: cold_batches)

    broker_calls: list[str] = []
    monkeypatch.setattr(webapp.job_client, "product", lambda key: broker_calls.append(("product", key)))
    terminalized: list[tuple] = []
    monkeypatch.setattr(webapp, "terminalize_operation", lambda *a, **k: terminalized.append((a, k)))
    try:
        webapp.complete_filesystem_watch_diff_operation(
            flight,
            {"mode": "full", "reason": "forced", "token": "", "removed_roots": []},
            roots,
            "seed",
        )
    finally:
        webapp.stop_jobd_operation_service()
        webapp.control_server.stop()

    assert terminalized == [], "a cancelled parent must publish no result or error"
    assert broker_calls == [], "the abandoned wait must not poll or cancel the broker; children stay reusable"


def test_retired_full_sse_watch_keyframe_path_has_no_production_reference():
    """The orphan periodic full-SSE keyframe subtree is gone; the accepted `/api/fs/watch-diff`
    operation is the only owner of full and diff frames, so no production module may still name it."""

    lib_root = app_module.Path(app_module.__file__).parent
    retired = (
        "publish_filesystem_ready_event",
        "publish_completed_filesystem_full_payload",
        "complete_filesystem_ready_event",
        "filesystem_watch_full_due",
        "mark_filesystem_watch_full_sent",
        "clear_filesystem_watch_full_inflight",
        "record_filesystem_watch_product_failure",
        "filesystem_full_inflight_token",
        "filesystem_last_full_at",
        "filesystem_payload_signature",
        "FILESYSTEM_WATCH_KEYFRAME_SECONDS",
    )
    offenders: dict[str, list[str]] = {}
    for path in sorted(lib_root.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        hits = sorted(name for name in retired if name in text)
        if hits:
            offenders[str(path.relative_to(lib_root))] = hits
    assert offenders == {}, f"retired full-SSE watch symbols still referenced in production: {offenders}"
