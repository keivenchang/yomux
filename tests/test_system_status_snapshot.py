# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""The `/api/system-status` background snapshot: one owner, typed refusals, lazy advanced work."""

from __future__ import annotations

from http import HTTPStatus
import json
from pathlib import Path
import re
import threading
import time
from types import SimpleNamespace
from typing import Any

import pytest

from yolomux_lib import http_routes
from yolomux_lib import system_status_snapshot

from tests.gate_harness import GateAuthCredentials
from tests.gate_harness import GateLiveServer
from tests.gate_harness import gate_auth_credentials  # noqa: F401
from tests.gate_harness import gate_authenticated_live_server  # noqa: F401
from tests.gate_harness import gate_http_port  # noqa: F401
from tests.gate_harness import gate_http_request
from tests.gate_harness import gate_runtime_paths  # noqa: F401
from tests.gate_harness import gate_tmux  # noqa: F401
from tests.helpers.http_routes import login_cookie as _login_cookie


REPO_ROOT = Path(__file__).resolve().parents[1]


class FakeClock:
    """One injected clock so a cadence/deadline test never sleeps."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = float(start)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)


def route_request(app: Any) -> tuple[Any, list[tuple[str, Any, Any]]]:
    """A minimal request double that records exactly which writer the route used."""

    writes: list[tuple[str, Any, Any]] = []
    request = SimpleNamespace(
        server=SimpleNamespace(app=app, dev=False),
        write_json=lambda value, status=HTTPStatus.OK: writes.append(("json", status, value)),
        write_json_bytes=lambda value, status=HTTPStatus.OK: writes.append(("json_bytes", status, value)),
        write_product_bytes=lambda data, product, promise=None: writes.append(("product", product, data)),
    )
    return request, writes


def owner_for(core: Any, advanced: Any, clock: FakeClock, **kwargs: Any) -> system_status_snapshot.SystemStatusSnapshotOwner:
    return system_status_snapshot.SystemStatusSnapshotOwner(
        build_core=core,
        build_advanced=advanced,
        monotonic=clock,
        wall_clock=clock,
        **kwargs,
    )


# ---------------------------------------------------------------------------------------------
# Box 1: one background owner publishes; the route is an O(1) read that assembles nothing.
# ---------------------------------------------------------------------------------------------


def test_route_serves_the_published_body_without_assembling_anything(make_tmux_webterm_app):
    """The route must hand back the exact published bytes and call no producer."""

    app = make_tmux_webterm_app(("1",))
    builds = {"core": 0, "advanced": 0}

    def build_core() -> dict[str, Any]:
        builds["core"] += 1
        return {"ok": True, "marker": f"core-{builds['core']}"}

    def build_advanced() -> dict[str, Any]:
        builds["advanced"] += 1
        return {"top_endpoints": []}

    clock = FakeClock()
    owner = owner_for(build_core, build_advanced, clock)
    app.attach_system_status_snapshot_owner(owner)
    owner.core.read()
    owner.publish_once()
    published = owner.core._snapshot
    assert builds == {"core": 1, "advanced": 0}

    request, writes = route_request(app)
    http_routes.get_system_status(request, None, None)

    assert builds == {"core": 1, "advanced": 0}, "the request thread must not have built anything"
    assert len(writes) == 1
    kind, product, data = writes[0]
    assert kind == "product", "the route must write pre-encoded bytes, not re-encode a dict"
    assert data is published.body, "the route must serve the published object, not a copy of it"
    assert product["length"] == len(published.body)
    assert json.loads(data.decode("utf-8"))["marker"] == "core-1"


def test_the_route_stays_flat_while_the_producer_is_slow(make_tmux_webterm_app):
    """A build that takes half a second must not be visible in the request path at all.

    This is the whole claim of the change, so it is asserted against a producer that is slow on
    purpose rather than against a fast fixture that would pass either way.
    """

    app = make_tmux_webterm_app(("1",))
    release = threading.Event()

    def build_core() -> dict[str, Any]:
        release.wait(0.5)
        return {"ok": True}

    clock = FakeClock()
    owner = owner_for(build_core, lambda: {}, clock)
    app.attach_system_status_snapshot_owner(owner)
    owner.core.read()
    owner.publish_once()

    worker = threading.Thread(target=owner.publish_once, daemon=True)
    started = time.perf_counter()
    worker.start()
    try:
        request, _writes = route_request(app)
        http_routes.get_system_status(request, None, None)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
    finally:
        release.set()
        worker.join(timeout=2.0)
    assert elapsed_ms < 100.0, f"the route waited {elapsed_ms:.1f}ms on a build in flight"


# ---------------------------------------------------------------------------------------------
# Box 2: typed unavailable and typed stale, never an aged body and never a synchronous rebuild.
# ---------------------------------------------------------------------------------------------


def test_read_before_the_first_publish_is_typed_unavailable_and_never_builds():
    clock = FakeClock()
    builds = []
    owner = owner_for(lambda: builds.append("core") or {"ok": True}, lambda: {}, clock)

    result = owner.read_core()

    assert builds == [], "a cold read must not rebuild on the reader's thread"
    assert result.state == system_status_snapshot.SNAPSHOT_STATE_UNAVAILABLE
    assert result.reason_code == system_status_snapshot.SNAPSHOT_UNAVAILABLE_REASON_CODE
    assert result.snapshot is None
    assert owner._wake.is_set(), "a cold read must ask the owner to build, not build itself"


def test_read_past_the_freshness_deadline_is_typed_stale_and_withholds_the_aged_body():
    clock = FakeClock()
    owner = owner_for(lambda: {"ok": True, "aged_body_marker": clock.now}, lambda: {}, clock, deadline_seconds=11.0)
    owner.core.read()
    owner.publish_once()
    assert owner.read_core().state == system_status_snapshot.SNAPSHOT_STATE_CURRENT

    clock.advance(11.001)
    result = owner.read_core()

    assert result.state == system_status_snapshot.SNAPSHOT_STATE_STALE
    assert result.reason_code == system_status_snapshot.SNAPSHOT_STALE_REASON_CODE
    assert result.snapshot is None, "a stale read must not hand back the aged body"
    assert result.age_seconds is not None and result.age_seconds > 11.0
    assert "11.0" in result.reason
    payload = result.refusal_payload(cadence_seconds=2.0, deadline_seconds=11.0)
    assert payload["ok"] is False
    assert payload["snapshot"]["state"] == "stale"
    assert payload["snapshot"]["last_generated_at"] == 1000.0
    assert "aged_body_marker" not in json.dumps(payload), "the refusal must not smuggle the aged body through"


def test_route_serves_the_typed_refusal_before_the_first_snapshot(make_tmux_webterm_app):
    app = make_tmux_webterm_app(("1",))
    clock = FakeClock()
    app.attach_system_status_snapshot_owner(owner_for(lambda: {"ok": True}, lambda: {}, clock))

    request, writes = route_request(app)
    http_routes.get_system_status(request, None, None)

    assert len(writes) == 1
    kind, _product, data = writes[0]
    assert kind == "product"
    body = json.loads(data.decode("utf-8"))
    assert body["ok"] is False
    assert body["snapshot"]["state"] == "unavailable"
    assert body["snapshot"]["reason_code"] == system_status_snapshot.SNAPSHOT_UNAVAILABLE_REASON_CODE
    assert body["snapshot"]["freshness_deadline_seconds"] == system_status_snapshot.FRESHNESS_DEADLINE_SECONDS
    assert body["snapshot"]["cadence_seconds"] == system_status_snapshot.SNAPSHOT_CADENCE_SECONDS


# ---------------------------------------------------------------------------------------------
# Box 3: advanced diagnostics come from their own retained producer, not from the 5 s poll.
# ---------------------------------------------------------------------------------------------


def test_advanced_diagnostics_are_not_built_by_the_core_cadence():
    clock = FakeClock()
    builds = {"core": 0, "advanced": 0}

    def build_core() -> dict[str, Any]:
        builds["core"] += 1
        return {"ok": True}

    def build_advanced() -> dict[str, Any]:
        builds["advanced"] += 1
        return {"top_endpoints": []}

    owner = owner_for(build_core, build_advanced, clock)
    for _poll in range(10):
        owner.read_core()
        owner.publish_once()
        clock.advance(system_status_snapshot.SNAPSHOT_CADENCE_SECONDS)

    assert builds["core"] == 10
    assert builds["advanced"] == 0, "nobody opened Advanced, so nothing may have produced it"

    owner.read_advanced()
    owner.publish_once()
    assert builds["advanced"] == 1


def test_the_core_payload_carries_no_advanced_diagnostics(make_tmux_webterm_app):
    """The 5 s body must not contain the fields only the Advanced disclosure reads."""

    app = make_tmux_webterm_app(("1",))
    core = app.system_status_core_payload()
    advanced = app.system_status_advanced_payload()

    for key in system_status_snapshot.SYSTEM_STATUS_ADVANCED_KEYS:
        assert key not in core, f"{key} is advanced-only and must not ride the 5 s poll"
        assert key in advanced, f"{key} must be produced by the advanced producer"
    assert "debug" not in core["owner"], "owner.debug is an Advanced card input"
    assert "debug" in advanced["owner"]
    # The roster the panel scans - not the disclosure it opens - stays in the cheap body.
    for key in ("local_services", "server", "cpu_budget", "host", "stats_current", "generated_at"):
        assert key in core


def test_advanced_route_serves_its_own_retained_producer(make_tmux_webterm_app):
    app = make_tmux_webterm_app(("1",))
    clock = FakeClock()
    owner = owner_for(lambda: {"ok": True}, lambda: {"ok": True, "top_endpoints": [{"surface": "x"}]}, clock)
    app.attach_system_status_snapshot_owner(owner)

    request, writes = route_request(app)
    http_routes.get_system_status_advanced(request, None, None)
    assert json.loads(writes[0][2].decode("utf-8"))["snapshot"]["state"] == "unavailable"

    owner.publish_once()
    request, writes = route_request(app)
    http_routes.get_system_status_advanced(request, None, None)
    body = json.loads(writes[0][2].decode("utf-8"))
    assert body["top_endpoints"] == [{"surface": "x"}]


# ---------------------------------------------------------------------------------------------
# Exactly one retained answer to this one question.
# ---------------------------------------------------------------------------------------------


def test_exactly_one_owner_constructs_the_retained_system_status_body():
    """A second retained system-status body would be the defect this module exists to prevent."""

    sources = [path for path in (REPO_ROOT / "yolomux_lib").rglob("*.py")]
    constructions = [
        f"{path.relative_to(REPO_ROOT)}:{number}"
        for path in sources
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if re.search(r"SystemStatusSnapshotOwner\s*\(", line) and "class " not in line
    ]
    assert len(constructions) == 1, f"expected one production owner, found {constructions}"

    attachments = [
        f"{path.relative_to(REPO_ROOT)}:{number}"
        for path in sources
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if "attach_system_status_snapshot_owner(" in line
        and "def " not in line
        and "system_status_projector_for(self)." not in line
    ]
    assert len(attachments) == 1, f"expected one attachment site, found {attachments}"


def test_the_route_module_contains_no_system_status_assembly():
    """The handlers must read a snapshot; a producer call in this module is assembly on the thread."""

    source = (REPO_ROOT / "yolomux_lib" / "http_routes.py").read_text(encoding="utf-8")
    for forbidden in ("system_status_core_payload(", "system_status_advanced_payload(", "runtime_report_payload("):
        assert forbidden not in source, f"{forbidden} must not be called from a request handler"


def test_slot_semantics_are_shared_by_both_slots():
    """Core and advanced must be the same class, or their freshness rules will drift apart."""

    clock = FakeClock()
    owner = owner_for(lambda: {"ok": True}, lambda: {"ok": True}, clock)
    assert type(owner.core) is type(owner.advanced) is system_status_snapshot.SnapshotSlot
    assert owner.core.deadline_seconds == owner.advanced.deadline_seconds


def test_nothing_rebuilds_while_nobody_is_reading():
    clock = FakeClock()
    builds = []
    owner = owner_for(lambda: builds.append(1) or {"ok": True}, lambda: {}, clock)

    for _cycle in range(5):
        owner.publish_once()
        clock.advance(60.0)
    assert builds == [], "an unread panel must not drive a rebuild loop"

    owner.read_core()
    owner.publish_once()
    assert len(builds) == 1

    clock.advance(system_status_snapshot.DEMAND_WINDOW_SECONDS + 1.0)
    owner.publish_once()
    assert len(builds) == 1, "demand must expire"


def test_a_failing_producer_is_counted_and_does_not_stop_the_other_slot():
    clock = FakeClock()
    reported: list[tuple[str, str]] = []

    def build_core() -> dict[str, Any]:
        raise RuntimeError("producer exploded")

    owner = owner_for(
        build_core,
        lambda: {"ok": True},
        clock,
        on_diagnostic=lambda name, error: reported.append((name, str(error))),
    )
    owner.read_core()
    owner.read_advanced()
    owner.publish_once()

    assert reported == [("core", "producer exploded")]
    assert owner.core.status()["build_failures"] == 1
    assert owner.advanced.status()["published"] is True
    assert owner.read_core().state == system_status_snapshot.SNAPSHOT_STATE_UNAVAILABLE


def test_start_is_idempotent_and_stop_joins_without_leaking_threads():
    before = {thread.ident for thread in threading.enumerate()}
    owner = system_status_snapshot.SystemStatusSnapshotOwner(
        build_core=lambda: {"ok": True},
        build_advanced=lambda: {"ok": True},
    )
    try:
        assert owner.start() is True
        assert owner.start() is False
        assert owner.running is True
    finally:
        owner.stop(timeout=5.0)
    assert owner.running is False
    assert owner.start() is False, "start-once must stay latched after stop"
    survivors = [
        thread.name for thread in threading.enumerate()
        if thread.ident not in before and thread.name == system_status_snapshot.SystemStatusSnapshotOwner.THREAD_NAME
    ]
    assert survivors == []


def test_the_app_starts_and_stops_exactly_one_producer(make_tmux_webterm_app):
    app = make_tmux_webterm_app(("1",))
    assert app.system_status_snapshot is None, "a unit-test app must not carry a producer it cannot stop"

    assert app.start_system_status_snapshot_owner() is True
    owner = app.system_status_snapshot
    try:
        assert owner.running is True
        assert app.start_system_status_snapshot_owner() is False, "a second start would be a second producer"
        assert app.system_status_snapshot is owner
    finally:
        app.stop_system_status_snapshot_owner()
    assert owner.running is False


def test_the_serving_process_owns_the_producer_lifecycle():
    """The producer belongs to the server that answers the route, and is retired with it."""

    source = (REPO_ROOT / "yolomux_lib" / "server.py").read_text(encoding="utf-8")
    assert "self.app.start_system_status_snapshot_owner()" in source
    # Stopped on the first shutdown signal AND at close, so a sealed fixture cannot see a build.
    assert source.count("self.app.stop_system_status_snapshot_owner()") == 2


def test_an_unattached_process_refuses_by_type_instead_of_building(make_tmux_webterm_app):
    """No owner is a real state - it must be named, not papered over with a synchronous build."""

    app = make_tmux_webterm_app(("1",))
    request, writes = route_request(app)
    http_routes.get_system_status(request, None, None)

    body = json.loads(writes[0][2].decode("utf-8"))
    assert body["ok"] is False
    assert body["snapshot"]["reason_code"] == system_status_snapshot.OWNER_UNATTACHED_REASON_CODE


def _poll_until_published(runtime: GateLiveServer, path: str, headers: dict[str, str], *, timeout: float = 10.0) -> dict[str, Any]:
    """Poll one snapshot route until it publishes, exactly as a client must after a typed refusal."""

    deadline = time.monotonic() + timeout
    body: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = gate_http_request(runtime, path, headers=headers, timeout=10.0)
        assert response.status == int(HTTPStatus.OK), response.status
        body = json.loads(response.body.decode("utf-8"))["data"]
        if body.get("ok") is not False:
            return body
        assert body["snapshot"]["state"] in {"unavailable", "stale"}
        time.sleep(0.05)
    raise AssertionError(f"{path} never published within {timeout}s: {body}")


def test_the_served_bodies_split_the_roster_from_the_advanced_diagnostics(
    gate_authenticated_live_server: GateLiveServer,  # noqa: F811
    gate_auth_credentials: GateAuthCredentials,  # noqa: F811
) -> None:
    """Over a real socket: the 5 s body carries the roster, and only the other route carries the rest."""

    runtime = gate_authenticated_live_server
    headers = {"Cookie": _login_cookie(runtime, gate_auth_credentials), "Connection": "close"}

    core = _poll_until_published(runtime, "/api/system-status", headers)
    assert core["ok"] is True
    assert isinstance(core["local_services"], dict) and core["local_services"]["schema_version"] == 4
    assert "generated_at" in core and "server" in core and "cpu_budget" in core
    for key in system_status_snapshot.SYSTEM_STATUS_ADVANCED_KEYS:
        assert key not in core, f"{key} must not ride the five-second poll"

    advanced = _poll_until_published(runtime, "/api/system-status/advanced", headers)
    for key in system_status_snapshot.SYSTEM_STATUS_ADVANCED_KEYS:
        assert key in advanced, f"{key} must be served by the advanced route"
    assert "debug" in advanced["owner"]

    status = runtime.app.system_status_snapshot.status()
    assert status["slots"]["core"]["builds"] >= 1
    assert status["slots"]["advanced"]["builds"] >= 1


@pytest.mark.parametrize("route_path", ["/api/system-status", "/api/system-status/advanced"])
def test_both_routes_are_registered_readonly_json(route_path: str):
    route = next(item for item in http_routes.ALL_ROUTES if item.method == "GET" and item.path == route_path)
    assert route.role == "readonly"
    assert route.protocol == http_routes.RESPONSE_JSON
