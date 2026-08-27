# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""`/readyz` and `/livez` reach a real daemon, and reach it without taking its locks.

`YOLO-V0717-E3-HEALTH-48` proved both verdict functions. What it could not do, because it did
not own `service.py`, was give them a source of control state -- so they were correct functions
with nothing to ask. These tests are about REACHABILITY and LOCK-FREEDOM, not about the verdicts,
which `tests/test_stats_current_health.py` already owns.
"""

from __future__ import annotations

import threading

import pytest
from pathlib import Path

from yolomux_lib import http_routes
from yolomux_lib.stats_current import http as stats_http
from yolomux_lib.stats_current import service as service_module


def _service(tmp_path):
    return service_module.StatsCurrentService(tmp_path / "stats.sock", tmp_path / "stats.sqlite3")


def _dispatch(service, action="resource_state"):
    """Go through the ROUTER, never the method. A direct call proves the body, not the route."""
    return service_module.STATS_COMMAND_ROUTER.dispatch(service, action, {"action": action}, b"")


def test_resource_state_is_reachable_through_the_command_router(tmp_path):
    """The gap this closes: the verdict functions existed with no way to be asked."""
    service = _service(tmp_path)

    assert "resource_state" in service_module.STATS_COMMAND_ROUTER.actions
    assert "resource_state" in service_module.CONTROL_FIELDS

    response = _dispatch(service)

    assert response is not None, "the router did not resolve resource_state to a handler"
    payload, body = response
    assert body == b""
    # Exactly the keys `http.readyz` reads. A missing key is a silent not-ready.
    for key in (
        "cache_generation", "pending_cells", "ring_failure", "materializer_state",
        "migration_state", "build_failed_since_publication", "owed_startup_slots",
    ):
        assert key in payload, f"resource_state omitted {key!r}, which readyz reads"


def test_resource_state_answers_while_work_lock_and_cache_lock_are_held(tmp_path):
    """The structural property, not a promise.

    `_status()` opens with `work_lock`, which the materializer worker holds across a build. A
    health endpoint that waits behind it reports nothing about the daemon it is checking. This
    fails the moment anyone routes the handler through `_status()`.
    """
    service = _service(tmp_path)
    answered: list[object] = []

    with service.work_lock, service.cache_lock:
        caller = threading.Thread(target=lambda: answered.append(_dispatch(service)))
        caller.start()
        caller.join(timeout=5.0)
        blocked = caller.is_alive()

    caller.join(timeout=5.0)
    assert not blocked, "resource_state blocked while work_lock and cache_lock were held"
    assert answered and answered[0] is not None


def test_readyz_over_the_routed_state_fails_closed_and_names_every_failing_condition(tmp_path):
    """Fail closed, and one poll must name every cause.

    A cold service has published nothing, so several conditions fail at once. Reporting only the
    first costs an operator one restart cycle per cause.
    """
    service = _service(tmp_path)
    payload, _ = _dispatch(service)

    sample = stats_http.ProcessSample(pid=1, sampled_at=0.0, exists=True, state="S")
    verdict = stats_http.readyz(sample, stats_http.StoreSizes(), payload)

    assert verdict.ok is False
    assert verdict.status == 503
    assert len(verdict.failures) >= 2, verdict.failures
    assert any("cache_generation" in item for item in verdict.failures), verdict.failures


def test_readiness_is_not_the_cache_ready_event(tmp_path):
    """The event fires on 6 of 6 cold starts with 1,248 cells still staged.

    If wiring made readiness depend on it, this passes while the daemon cannot serve the window
    it claims to be ready for.
    """
    service = _service(tmp_path)
    service.cache_ready_event.set()
    service._pending_ring_dirty = {object()}

    payload, _ = _dispatch(service)
    assert payload["pending_cells"] == 1

    sample = stats_http.ProcessSample(pid=1, sampled_at=0.0, exists=True, state="S")
    verdict = stats_http.readyz(sample, stats_http.StoreSizes(), payload)

    assert service.cache_ready_event.is_set() is True
    assert verdict.ok is False
    assert any("pending_cells" in item for item in verdict.failures), verdict.failures


def test_an_unreachable_daemon_is_not_ready_rather_than_defaulting_to_ready(tmp_path):
    """No silent default. Absent control state is not-ready, never ready."""
    sample = stats_http.ProcessSample(pid=1, sampled_at=0.0, exists=True, state="S")

    verdict = stats_http.readyz(sample, stats_http.StoreSizes(), None)

    assert verdict.ok is False
    assert any("resource_state unavailable" in item for item in verdict.failures), verdict.failures


# --- the URL table -----------------------------------------------------------------------------
#
# The router half above proves the daemon can be ASKED. These prove the endpoints can be
# REACHED. They are separate layers and a test of one says nothing about the other: before this,
# `readyz()` and `livez()` were correct functions whose only callers were tests.


class _FakeStatsHttp:
    """Records what the handlers ask of statsd, so a test can assert what they did NOT ask."""

    def __init__(self, resource_state=None):
        self.client = self
        self._resource_state = resource_state
        self.database_path = Path("/nonexistent/stats.sqlite3")
        self.status_calls = 0
        self.resource_state_calls = 0

    def status(self):
        self.status_calls += 1
        return {}

    def resource_state(self):
        self.resource_state_calls += 1
        return dict(self._resource_state or {})


class _FakeRequest:
    def __init__(self, stats_http, database_path):
        self.written: list[tuple[object, object]] = []

        class _App:
            pass

        class _Server:
            pass

        self.server = _Server()
        self.server.app = _App()
        self.server.app.stats_current_http = stats_http
        self.server.app.stats_current_database_path = database_path

    def write_json(self, payload, status=None):
        self.written.append((payload, status))


@pytest.fixture(autouse=True)
def _clear_readyz_cache():
    """`_LAST_READYZ` is module state by design -- it is what lets /livez stop asking statsd.

    Module state shared across tests is a real ordering hazard, so every test in this file starts
    from empty rather than inheriting a pid another test cached.
    """
    http_routes._LAST_READYZ.clear()
    yield
    http_routes._LAST_READYZ.clear()


def _route(path):
    return http_routes.route_for_request("GET", path)


def test_readyz_and_livez_resolve_through_the_url_table(tmp_path):
    """The gap the release note was walked back for: no reachable URL.

    `route_for_request` is the same resolution the listener performs, so a route it cannot find
    is a 404 however correct the verdict function behind it is.
    """
    for path in ("/readyz", "/livez"):
        route = _route(path)
        assert route is not None, f"{path} does not resolve through the URL table"
        assert route.method == "GET"
        assert callable(route.handler)


def test_readyz_over_the_url_fails_closed_and_names_every_failing_condition(tmp_path):
    """Fail closed on the URL path too, and one poll must name every cause."""
    stats_http = _FakeStatsHttp(resource_state={})
    request = _FakeRequest(stats_http, tmp_path / "stats.sqlite3")

    _route("/readyz").handler(request, None, _route("/readyz"))

    assert request.written, "the /readyz handler wrote no response"
    payload, status = request.written[0]
    assert status == 503
    assert payload["ready"] is False
    assert len(payload["failures"]) >= 2, payload["failures"]


def test_neither_endpoint_asks_statsd_for_status(tmp_path):
    """`_status()` opens with `work_lock`. Neither URL may reach it, however convenient it is."""
    stats_http = _FakeStatsHttp(resource_state={})
    request = _FakeRequest(stats_http, tmp_path / "stats.sqlite3")

    _route("/readyz").handler(request, None, _route("/readyz"))
    _route("/livez").handler(request, None, _route("/livez"))

    assert stats_http.status_calls == 0, "a health endpoint routed through _status()"


def test_livez_does_not_enter_statsd_at_all(tmp_path):
    """`/livez` is computed from `/proc` by the CALLER, and that is its whole purpose.

    The worker holds the GIL for the full build burst, so an endpoint that enters the daemon
    cannot detect the wedge it exists to detect. `has_outstanding_work` therefore comes from a
    CACHED prior `/readyz`, never a fresh in-process read.
    """
    # A real daemon always reports its own pid, which is what lets /livez stop asking.
    stats_http = _FakeStatsHttp(resource_state={"pid": 4242})
    request = _FakeRequest(stats_http, tmp_path / "stats.sqlite3")

    # One /readyz first, which is what a supervisor does. After it, /livez asks statsd NOTHING.
    _route("/readyz").handler(request, None, _route("/readyz"))
    before = stats_http.resource_state_calls
    request.written.clear()

    _route("/livez").handler(request, None, _route("/livez"))

    assert stats_http.resource_state_calls == before, (
        "/livez issued a control call, reacquiring the GIL dependency it exists to avoid"
    )
    assert stats_http.status_calls == 0
    payload, _status = request.written[0]
    assert "live" in payload


# --- auth ---------------------------------------------------------------------------------------
#
# `/readyz` reports pending cell counts, migration state and failure strings. That is internal
# operational detail about a running daemon and must not be world-readable. `/livez` may stay
# public, but only on the terms `get_healthz` already sets: it says in as many words that
# "reporting anything richer would leak system state to an unauthenticated caller".


class _AuthRequest(_FakeRequest):
    """Records whether the dispatcher let the handler run at all."""

    def __init__(self, stats_http, database_path, *, authorised: bool):
        super().__init__(stats_http, database_path)
        self._authorised = authorised
        self.auth_checks: list[str] = []

    def require_auth(self, role):
        self.auth_checks.append(role)
        return self._authorised

    def auth_readonly(self):
        return False

    def auth_identity(self):
        return None

    def reject_forbidden(self, identity, role):
        raise AssertionError("unexpected forbidden rejection")


def _dispatch_url(request, path):
    """Through the DISPATCHER, so the route's own auth rule is what decides -- not the handler."""
    route = _route(path)
    return http_routes._dispatch_route_handler(request, None, route)


def test_readyz_refuses_an_unauthenticated_request(tmp_path):
    """Fail closed. The refusal must happen BEFORE the handler runs, not inside it.

    Asserting that an authenticated request succeeds proves nothing about the unauthenticated one,
    which is the case that matters -- so this asserts the handler never ran and nothing was written.
    """
    stats_http = _FakeStatsHttp(resource_state={"pid": 4242})
    request = _AuthRequest(stats_http, tmp_path / "stats.sqlite3", authorised=False)

    _dispatch_url(request, "/readyz")

    assert _route("/readyz").role != http_routes.PUBLIC, "/readyz is world-readable"
    assert request.auth_checks, "the dispatcher never applied an auth rule to /readyz"
    assert request.written == [], "an unauthenticated caller was served /readyz"
    assert stats_http.resource_state_calls == 0, "an unauthenticated caller reached statsd"


def test_readyz_is_served_to_an_authenticated_caller(tmp_path):
    """The other half: authenticating it must not have broken it."""
    stats_http = _FakeStatsHttp(resource_state={"pid": 4242})
    request = _AuthRequest(stats_http, tmp_path / "stats.sqlite3", authorised=True)

    _dispatch_url(request, "/readyz")

    assert request.written, "an authenticated caller was not served /readyz"
    payload, _status = request.written[0]
    assert "failures" in payload


def test_readyz_reuses_the_existing_route_auth_owner(tmp_path):
    """One auth rule, not a parallel one beside it.

    The role on the Route is what `_dispatch_route_handler` enforces for every other authenticated
    route in this table. A second auth check inside the handler would be the divergent copy.
    """
    assert _route("/readyz").role == "readonly"
    assert _route("/healthz").role == http_routes.PUBLIC


def test_public_livez_reports_no_more_than_the_existing_public_health_contract(tmp_path):
    """`get_healthz` sets the terms for every unauthenticated health answer in this table.

    It writes `{"ok": True}` and says reporting anything richer would leak system state to an
    unauthenticated caller. A public `/livez` that returned the process sample -- pid, run state,
    CPU ticks, IO byte counters, context switches -- would be exactly that.
    """
    stats_http = _FakeStatsHttp(resource_state={"pid": 4242})
    request = _AuthRequest(stats_http, tmp_path / "stats.sqlite3", authorised=False)

    _dispatch_url(request, "/livez")

    assert _route("/livez").role == http_routes.PUBLIC
    assert request.written, "the public liveness probe was refused"
    payload, _status = request.written[0]
    leaked = {"pid", "cpu_ticks", "read_bytes", "write_bytes", "voluntary_ctxt_switches", "state"}
    assert not (leaked & set(payload)), f"/livez leaked {sorted(leaked & set(payload))} publicly"
    assert set(payload) <= {"ok", "live"}, sorted(payload)


def test_each_health_handler_docstring_agrees_with_its_route_role(tmp_path):
    """Source prose and route security must agree, and a test outlives an instruction.

    `get_readyz` documented itself as "Unauthenticated like /healthz" for as long as it was
    public, and the sentence survived the change that authenticated it -- so the handler
    described the opposite of its own Route. A reader could not tell a deliberate property from a
    leftover default, which is the failure this pins rather than merely warns about.
    """
    expectations = (
        ("/readyz", "readonly", "AUTHENTICATED", "PUBLIC, and public for a reason"),
        ("/livez", http_routes.PUBLIC, "PUBLIC, and public for a reason", "AUTHENTICATED"),
        ("/healthz", http_routes.PUBLIC, "unauthenticated", "AUTHENTICATED"),
    )
    for path, role, must_say, must_not_say in expectations:
        route = _route(path)
        doc = route.handler.__doc__ or ""
        assert route.role == role, f"{path} role is {route.role!r}, want {role!r}"
        assert must_say in doc, f"{path} docstring does not state its auth posture"
        assert must_not_say not in doc, f"{path} docstring claims the opposite posture"


def test_the_public_liveness_docstring_carries_the_do_not_widen_instruction(tmp_path):
    """The leak happened because a rule about CONTENT was read as one about liveness.

    An explanation can be re-reasoned away by the next editor; an instruction in the place they
    are already looking cannot be missed. This asserts the instruction is where they will look.
    """
    doc = _route("/livez").handler.__doc__ or ""

    assert "DO NOT WIDEN" in doc
    assert "before any operator cookie exists" in doc, "the reason /livez is public is not stated"
