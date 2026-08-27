# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

from __future__ import annotations

import json
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any
from urllib.parse import parse_qs
from urllib.parse import unquote
from urllib.parse import urlparse

from .infra.common import ACTIVITY_MAX_HOURS
from .infra.common import MAX_COMPACT_TRANSCRIPT_ITEMS
from .infra.common import MAX_EVENT_TAIL_LINES
from .infra.common import MAX_TRANSCRIPT_TAIL_LINES
from .infra.common import auth_setup_required
from .infra.common import error_payload
from .infra.common import inline_json_product_metadata
from .infra.common import parse_bool
from .state_services import ClientWatchRootValidationError
from .chat.chat_service import ChatServiceError
from .chat.chat_store import ChatStoreValidationError
from .workspace.locales import resolve_locale_preference
from .stats_current import http as stats_health
from .web import html_page
from .web import server_string
from .web import static_content_type
from .server_logs import server_logs_payload
from .statusd_protocol import activity_summary_disabled_response
from .statusd_protocol import activity_summary_enabled


RouteRole = str | Callable[[Any, Any], str]
RouteHandler = Callable[[Any, Any, "Route"], bool | None]
PUBLIC = "public"
RESPONSE_JSON = "json"
RESPONSE_JSON_BATCH = "json-batch"
RESPONSE_SSE = "sse"
RESPONSE_BINARY = "binary"
RESPONSE_HTML = "html"
RESPONSE_REDIRECT = "redirect"
RESPONSE_STATIC = "static"
RESPONSE_WEBSOCKET = "websocket"
RESPONSE_PROTOCOLS = frozenset({
    RESPONSE_JSON,
    RESPONSE_JSON_BATCH,
    RESPONSE_SSE,
    RESPONSE_BINARY,
    RESPONSE_HTML,
    RESPONSE_REDIRECT,
    RESPONSE_STATIC,
    RESPONSE_WEBSOCKET,
})


class RequestValidationError(str):
    """String-compatible validation detail carrying the shared message descriptor fields."""

    def __new__(cls, fallback: str, message_key: str, **message_params: Any):
        value = super().__new__(cls, fallback)
        value.message_key = message_key
        value.message_params = message_params
        value.diagnostic = ""
        return value

    def payload(self, *, status: int = HTTPStatus.BAD_REQUEST) -> dict[str, Any]:
        return error_payload(
            self,
            message_key=self.message_key,
            message_params=self.message_params,
            diagnostic=self.diagnostic,
            status=status,
        )


@dataclass(frozen=True)
class Route:
    method: str
    path: str
    role: RouteRole
    handler: RouteHandler
    protocol: str
    body_limit: int | None = None
    group: str = "core"
    normal_session_local_service: bool = False

    def __post_init__(self) -> None:
        if self.protocol not in RESPONSE_PROTOCOLS:
            raise ValueError(f"invalid response protocol: {self.protocol}")


def query_one(qs: dict[str, list[str]], name: str, default: str | None = "") -> str | None:
    values = qs.get(name)
    return values[0] if values else default


def request_query(request: Any, parsed: Any) -> dict[str, list[str]]:
    """Return the request's parsed query once, for every route helper that needs it."""
    cached = getattr(request, "_route_query_cache", None)
    if cached is not None and cached[0] is parsed:
        return cached[1]
    qs = parse_qs(parsed.query)
    setattr(request, "_route_query_cache", (parsed, qs))
    return qs


def query_list(qs: dict[str, list[str]], name: str) -> list[str]:
    values: list[str] = []
    for raw_value in qs.get(name, []):
        for item in str(raw_value or "").split(","):
            value = item.strip()
            if value:
                values.append(value)
    return values


def query_bool(qs: dict[str, list[str]], name: str, default: bool = False) -> bool:
    raw_default = "1" if default else "0"
    return parse_bool(str(query_one(qs, name, raw_default) or ""))


def session_param(qs: dict[str, list[str]], default: str | None = "") -> str | None:
    """Return the optional session query once with the route's explicit missing-value contract."""
    value = query_one(qs, "session", default)
    return None if value is None else str(value or "")


def client_ip(request: Any) -> str:
    """Return a client address when the request transport supplied one."""
    address = request.client_address
    return str(address[0]) if isinstance(address, tuple) and address else ""


def parse_query_int(
    qs: dict[str, list[str]],
    name: str,
    default: int,
    *,
    min_value: int = 1,
    max_value: int | None = None,
    clamp_min: bool = False,
) -> tuple[int | None, str]:
    raw = qs.get(name, [str(default)])[0]
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None, RequestValidationError(
            f"{name} must be an integer",
            "request.error.integer",
            field=name,
        )
    if value < min_value:
        if clamp_min:
            value = min_value
        else:
            return None, RequestValidationError(
                f"{name} must be at least {min_value}",
                "request.error.minimum",
                field=name,
                min=min_value,
            )
    if max_value is not None:
        value = min(value, max_value)
    return value, ""


def parse_query_float(
    qs: dict[str, list[str]],
    name: str,
    default: float,
    *,
    min_value: float = 0.0,
    max_value: float | None = None,
) -> tuple[float | None, str]:
    raw = qs.get(name, [str(default)])[0]
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None, RequestValidationError(
            f"{name} must be a number",
            "request.error.number",
            field=name,
        )
    if not math.isfinite(value):
        return None, RequestValidationError(
            f"{name} must be finite",
            "request.error.finite",
            field=name,
        )
    if value < min_value:
        return None, RequestValidationError(
            f"{name} must be at least {min_value:g}",
            "request.error.minimum",
            field=name,
            min=f"{min_value:g}",
        )
    if max_value is not None:
        value = min(value, max_value)
    return value, ""


def parse_repo_refs_param(raw: str | None) -> dict[str, dict[str, str]] | None:
    # C6: decode the optional per-repo FROM/TO JSON map sent as URL-encoded JSON
    # ({repo_path: {"from": <ref>, "to": <ref>}}). Returns None for absent/malformed input so the caller
    # falls back to the scalar from/to; only well-formed string ref pairs survive.
    if not raw:
        return None
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(decoded, dict):
        return None
    result: dict[str, dict[str, str]] = {}
    for repo, refs in decoded.items():
        if not isinstance(repo, str) or not isinstance(refs, dict):
            continue
        entry: dict[str, str] = {}
        for key in ("from", "to"):
            value = refs.get(key)
            if isinstance(value, str) and value.strip():
                entry[key] = value.strip()
        if entry:
            result[repo] = entry
    return result or None


def route_required_role(route: Route, request: Any, parsed: Any) -> str | None:
    role = route.role(request, parsed) if callable(route.role) else route.role
    return None if role == PUBLIC else role


def route_matches(route: Route, path: str) -> bool:
    if "*" not in route.path:
        return path == route.path
    prefix, suffix = route.path.split("*", 1)
    return path.startswith(prefix) and path.endswith(suffix) and len(path) > len(prefix) + len(suffix)


def routes_for_method(method: str) -> tuple[Route, ...]:
    return ROUTES_BY_METHOD.get(method.upper(), ())


def route_for_request(method: str, path: str) -> Route | None:
    for route in routes_for_method(method):
        if route_matches(route, path):
            return route
    return None


def dispatch_http_route(request: Any, method: str) -> None:
    # A Handler instance can serve many HTTP/1.1 requests.  The server resets its request
    # record before parsing each request; route dispatch supplies the per-request start used
    # for ordinary endpoints which do not have a more specific build timer.
    setattr(request, "_http_request_dispatch_started_at", time.perf_counter())
    setattr(request, "_http_request_thread_cpu_started_ns", time.thread_time_ns())
    setattr(request, "_http_request_thread_native_id", threading.get_native_id())
    parsed = urlparse(request.path)
    if request.redirect_plaintext_to_https_if_needed(parsed):
        return

    route = route_for_request(method, parsed.path)
    if route is None:
        _write_not_found_after_default_auth(request, method)
        return

    request.dispatch_route_response(route, lambda: _dispatch_route_handler(request, parsed, route))


def _dispatch_route_handler(request: Any, parsed: Any, route: Route) -> None:
    if route.role == PUBLIC:
        handled = route.handler(request, parsed, route)
        if handled is False:
            _write_not_found_after_default_auth(request, route.method)
        return

    required_role = route_required_role(route, request, parsed)
    if required_role is not None and not request.require_auth(required_role):
        return
    if route.group == "filesystem" and request.auth_readonly():
        request.reject_forbidden(request.auth_identity(), "admin")
        return
    route.handler(request, parsed, route)


def _write_not_found_after_default_auth(request: Any, method: str) -> None:
    if method.upper() == "GET":
        if not request.require_auth("readonly"):
            return
        locale = resolve_locale_preference(request.request_locale_pref(), request.headers.get("Accept-Language", ""))
        request.write_text(server_string(locale, "common.notFound") + "\n", status=HTTPStatus.NOT_FOUND)
        return
    if not request.require_auth("admin"):
        return
    request.write_json(
        error_payload("not found", message_key="common.notFound", status=HTTPStatus.NOT_FOUND),
        status=HTTPStatus.NOT_FOUND,
    )


def require_json_body(request: Any, route: Route, *, allow_empty: bool = False, allow_missing: bool = False) -> dict[str, Any] | None:
    if route.body_limit is None:
        raise RuntimeError(f"route {route.method} {route.path} has no body_limit")
    if not allow_empty and not allow_missing:
        return request.read_json_body(route.body_limit)
    return request.read_json_body(route.body_limit, allow_empty=allow_empty, allow_missing=allow_missing)


def get_static_asset(request: Any, parsed: Any, route: Route) -> bool:
    del route
    asset = parsed.path.removeprefix("/static/")
    content_type = static_content_type(asset)
    if not content_type:
        return False
    request.write_static_asset(asset, content_type)
    return True


def get_auth_setup(request: Any, parsed: Any, route: Route) -> None:
    del parsed, route
    request.write_json({"setup_required": auth_setup_required()})


def get_login(request: Any, parsed: Any, route: Route) -> None:
    del route
    request.handle_login_page(parsed)


def get_logout(request: Any, parsed: Any, route: Route) -> None:
    del parsed, route
    request.write_redirect("/login", clear_auth=True)


def get_healthz(request: Any, parsed: Any, route: Route) -> None:
    """Answer the process supervisor's unauthenticated liveness probe from the HTTP listener alone.

    boot.sh polls this while restarting, before any operator cookie exists. It must never consult
    tmux, jobd, watchd, statusd, the filesystem, or any local service: this is liveness for the
    listener, not readiness for the system. Reporting anything richer would both leak system state
    to an unauthenticated caller and make an unrelated subsystem able to fail a restart.
    """
    del parsed, route
    request.write_json({"ok": True})


# The last `/readyz` answer, so `/livez` can read `has_outstanding_work` WITHOUT entering statsd.
#
# This cache is the whole reason `/livez` works. It is computed entirely from `/proc` by this
# process, so it never takes statsd's GIL -- and the worker holds that GIL for the full 800-940 ms
# build burst, which is exactly the wedge `/livez` exists to detect. An endpoint that asked the
# daemon "are you busy?" could not answer while the daemon was too busy to reply.
#
# DO NOT "simplify" this to a fresh control call. It reads like a stale value being preferred to a
# live one; it is the opposite -- a live read reacquires the dependency the endpoint is built to
# avoid, and the endpoint stops detecting the only failure it is for.
_LAST_READYZ: dict[str, Any] = {}


def _statsd_pid(client: Any) -> int:
    """statsd's pid, learned ONCE and then never asked for again.

    A pid is a constant for the process lifetime, so a single bootstrap call is categorically
    different from polling the daemon for its state on every probe -- which is the thing `/livez`
    must never do. After the first `/readyz`, or after this one call, `/livez` asks statsd nothing.
    """
    cached = _LAST_READYZ.get("pid")
    if cached:
        return int(cached)
    pid = int(client.resource_state().get("pid") or 0)
    if pid:
        _LAST_READYZ["pid"] = pid
    return pid


def _statsd_process_sample(request: Any) -> Any:
    client = request.server.app.stats_current_http.client
    return stats_health.read_process_sample(_statsd_pid(client))


def get_readyz(request: Any, parsed: Any, route: Route) -> None:
    """Can statsd serve a CORRECT snapshot right now. Fails closed, and names every cause.

    AUTHENTICATED -- `role="readonly"` on its Route, enforced by `_dispatch_route_handler` before
    this function is ever called. Do not add an auth check in here; one rule beside another is how
    the two drift. This answer carries pending cell counts, migration state, failure strings and
    the process sample, which is internal operational detail about a running daemon and is exactly
    what must not be world-readable. `/livez` below is the public tier and stays narrow.

    It reaches statsd, unlike `/healthz`, but through `resource_state`, which takes no lock --
    never `status()`, which opens with `work_lock`, the lock the materializer worker holds across
    a build. A probe that waits behind the daemon it is checking reports nothing about it.

    Every failing condition is reported, not the first: one cause per poll costs an operator one
    restart cycle per cause.
    """
    del parsed, route
    client = request.server.app.stats_current_http.client
    sample = _statsd_process_sample(request)
    sizes = stats_health.read_store_sizes(client.database_path)
    # Absent control state is NOT READY. `readyz` treats an empty mapping as unreachable rather
    # than assuming health, so a daemon that cannot answer never asserts its own readiness.
    control = client.resource_state()
    verdict = stats_health.readyz(sample, sizes, control)
    _LAST_READYZ["sample"] = sample
    _LAST_READYZ["payload"] = verdict.payload
    if control.get("pid"):
        _LAST_READYZ["pid"] = int(control["pid"])
    request.write_json(verdict.payload, status=verdict.status)


def get_livez(request: Any, parsed: Any, route: Route) -> None:
    """Is statsd capable of making progress. Nothing else, and nothing that enters it.

    PUBLIC, and public for a reason rather than by default: a process supervisor polls a liveness
    probe **while restarting, before any operator cookie exists**. Authenticating it would break
    that restart. `get_healthz` above is the same tier for the same reason.

    **DO NOT WIDEN THIS RESPONSE.** Public here means NARROW: `{"ok": ..., "live": ...}` and
    nothing else. `get_healthz` states the rule -- reporting anything richer leaks system state to
    an unauthenticated caller -- and that is a rule about CONTENT, not about liveness versus
    readiness. Reading it the other way is how this endpoint shipped ten keys to unauthenticated
    callers, six of them process detail: pid, run state, CPU ticks, both IO byte counters and
    context switches. `verdict.payload` still carries all of that; only the verdict is published.
    The detailed projection belongs on authenticated `/readyz`, which already returns it.

    Computed from `/proc` alone. `has_outstanding_work` and the previous sample come from the
    cached prior `/readyz` -- see `_LAST_READYZ`. When no `/readyz` has run yet neither is
    supplied and `livez()` applies its own documented defaults; a value is not invented here.
    """
    del parsed, route
    sample = _statsd_process_sample(request)
    previous = _LAST_READYZ.get("sample")
    cached = _LAST_READYZ.get("payload")
    if cached is None:
        verdict = stats_health.livez(sample, previous)
    else:
        verdict = stats_health.livez(
            sample, previous, has_outstanding_work=bool(cached.get("pending_cells")),
        )
    # NARROW, because this is unauthenticated. `get_healthz` above sets the terms for every public
    # health answer in this table: it writes `{"ok": True}` and says reporting anything richer
    # would leak system state to an unauthenticated caller. `verdict.payload` carries the process
    # sample -- pid, run state, CPU ticks, IO byte counters, context switches -- so it is exactly
    # what that sentence forbids. The verdict itself is all a supervisor needs, and the full
    # projection is available to an authenticated caller on `/readyz`.
    request.write_json({"ok": verdict.ok, "live": verdict.ok}, status=verdict.status)


def get_ping(request: Any, parsed: Any, route: Route) -> None:
    del parsed, route
    request.write_json({"ok": True, "time": time.time()})


def get_stats_snapshot(request: Any, parsed: Any, route: Route) -> None:
    del route
    result = request.server.app.stats_current_http.snapshot(
        parsed.query,
        authenticated_username=request.auth_identity().username,
    )
    if result.payload is not None:
        request.write_json(result.payload, status=result.status)
    elif result.status == HTTPStatus.OK:
        request.write_product_bytes(result.body, inline_json_product_metadata(result.body))
    else:
        request.write_json_bytes(result.body, status=result.status)


def get_stats_delta(request: Any, parsed: Any, route: Route) -> None:
    del route
    result = request.server.app.stats_current_http.delta(
        parsed.query,
        authenticated_username=request.auth_identity().username,
    )
    if result.payload is not None:
        request.write_json(result.payload, status=result.status)
    elif result.status == HTTPStatus.OK:
        request.write_product_bytes(result.body, inline_json_product_metadata(result.body))
    else:
        request.write_json_bytes(result.body, status=result.status)


def get_stats_stream(request: Any, parsed: Any, route: Route) -> None:
    del route
    request.stream_stats_current(
        parsed.query,
        authenticated_username=request.auth_identity().username,
    )


def get_stats_capabilities(request: Any, parsed: Any, route: Route) -> None:
    del parsed, route
    request.write_json(request.server.app.stats_current_http.capabilities())


def post_stats_retry(request: Any, parsed: Any, route: Route) -> None:
    del parsed, route
    payload = request.server.app.stats_current_http.retry()
    request.write_json(payload, status=HTTPStatus.OK if payload.get("ok") is True else HTTPStatus.FAILED_DEPENDENCY)


def get_pricing_catalog(request: Any, parsed: Any, route: Route) -> None:
    del parsed, route
    # This status path is intentionally local/instant: it may initialize an
    # offline seed cache but never performs a provider request.
    request.write_json(request.server.app.pricing_catalog_status_payload())


def post_pricing_catalog_refresh(request: Any, parsed: Any, route: Route) -> None:
    del parsed, route
    # The coordinator owns single-flight and starts its daemon worker; an HTTP
    # handler must never wait for a provider crawl.
    request.write_json(request.server.app.pricing_catalog_refresh_start(), status=HTTPStatus.ACCEPTED)


def post_stats_observations(request: Any, parsed: Any, route: Route) -> None:
    del parsed
    if route.body_limit is None:
        raise RuntimeError(f"route {route.method} {route.path} has no body_limit")
    body, error, status = request.read_request_body(route.body_limit)
    if error is not None:
        request.write_json(error, status=status)
        return
    response, status = request.server.app.record_current_browser_observations(
        body,
        authenticated_username=request.auth_identity().username,
    )
    request.write_json(response, status=status)


def get_update_status(request: Any, parsed: Any, route: Route) -> None:
    del route
    if request.auth_readonly():
        request.reject_forbidden(request.auth_identity(), "admin")
        return
    request.write_json(request.server.app.update_status_payload(dryrun=query_bool(request_query(request, parsed), "dryrun")))


def get_dev_reload(request: Any, parsed: Any, route: Route) -> None:
    del route
    if not getattr(request.server, "dev", False):
        request.write_json(
            error_payload("not found", message_key="common.notFound", status=HTTPStatus.NOT_FOUND),
            status=HTTPStatus.NOT_FOUND,
        )
        return
    request.stream_dev_reload(str(query_one(request_query(request, parsed), "bundle_revision", "") or ""))


def get_client_events(request: Any, parsed: Any, route: Route) -> None:
    del route
    qs = request_query(request, parsed)
    operation_id = str(query_one(qs, "operation_id", "") or "")
    replay_operation_ids = tuple(dict.fromkeys(
        item[:128]
        for item in str(query_one(qs, "operations", "") or "").split(",")[:64]
        if item
    ))
    request.stream_client_events(
        channels=str(query_one(qs, "channels", "") or ""),
        client_id=str(query_one(qs, "client_id", "") or ""),
        operation_id=operation_id,
        replay_operation_ids=replay_operation_ids,
    )


def get_operation(request: Any, parsed: Any, route: Route) -> None:
    del route
    operation_id = unquote(parsed.path.removeprefix("/api/operations/")).strip("/")
    request.write_app_result(request.server.app.operation_status_payload(operation_id))


def post_operation_acknowledgments(request: Any, parsed: Any, route: Route) -> None:
    del parsed
    payload = require_json_body(request, route)
    if payload is None:
        return
    raw_acknowledgments = payload.get("acks")
    if not isinstance(raw_acknowledgments, list) or not 1 <= len(raw_acknowledgments) <= 64:
        request.write_json(
            error_payload("operation acknowledgments required", message_key="common.requestFailed", status=HTTPStatus.BAD_REQUEST),
            status=HTTPStatus.BAD_REQUEST,
        )
        return
    acknowledgments: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_acknowledgments:
        operation_id = str(raw.get("id") or "") if isinstance(raw, dict) else ""
        cursor = raw.get("cursor") if isinstance(raw, dict) else None
        epoch = str(cursor.get("epoch") or "") if isinstance(cursor, dict) else ""
        seq = cursor.get("seq") if isinstance(cursor, dict) else None
        if (
            not isinstance(raw, dict)
            or set(raw) != {"id", "cursor"}
            or not isinstance(cursor, dict)
            or set(cursor) != {"epoch", "seq"}
            or not operation_id
            or len(operation_id) > 128
            or not epoch
            or len(epoch) > 128
            or not isinstance(seq, int)
            or isinstance(seq, bool)
            or seq <= 0
            or operation_id in seen
        ):
            request.write_json(
                error_payload("invalid operation acknowledgment", message_key="common.requestFailed", status=HTTPStatus.BAD_REQUEST),
                status=HTTPStatus.BAD_REQUEST,
            )
            return
        seen.add(operation_id)
        acknowledgments.append({"id": operation_id, "cursor": {"epoch": epoch, "seq": seq}})
    request.write_app_result(request.server.app.acknowledge_operation_deliveries(acknowledgments))


def get_home(request: Any, parsed: Any, route: Route) -> None:
    del parsed, route
    sessions = request.server.app.sessions
    recent_sessions = request.server.app.tmux_recency_ordered_sessions(sessions)
    started = time.perf_counter()
    body = html_page(
        sessions,
        request.auth_identity().role,
        dev=getattr(request.server, "dev", False),
        dangerously_yolo=request.server.app.dangerously_yolo,
        accept_language=getattr(request, "headers", {}).get("Accept-Language", ""),
        auth_username=request.auth_identity().username,
        recent_sessions=recent_sessions,
    )
    compute_ms = (time.perf_counter() - started) * 1000
    setattr(request, "_http_response_compute_ms", compute_ms)
    setattr(request, "_http_response_performance_details", {
        "html_page": True,
        "bootstrap_bytes": len(body.encode("utf-8")),
        "session_count": len(sessions),
    })
    request.write_html(body)


def get_preview_popout(request: Any, parsed: Any, route: Route) -> None:
    del route
    request.handle_preview_popout_placeholder(parsed)


def get_pane_popout(request: Any, parsed: Any, route: Route) -> None:
    del route
    request.handle_pane_popout_placeholder(parsed)


def session_metadata_route_payload(request: Any, parsed: Any) -> dict[str, Any]:
    qs = request_query(request, parsed)
    payload_fn = request.server.app.session_metadata_payload
    return payload_fn(force=query_bool(qs, "force"))


def get_session_metadata(request: Any, parsed: Any, route: Route) -> None:
    del route
    payload = session_metadata_route_payload(request, parsed)
    request.write_json({
        "state": "ready",
        "request": {"id": request.api_request_id()},
        "data": payload,
    })


def get_transcripts(request: Any, parsed: Any, route: Route) -> None:
    del route
    # Keep the old flattened shape on the explicitly documented compatibility alias. The current
    # browser uses /api/session-metadata and the shared ready-envelope decoder, so duplicating the
    # multi-megabyte metadata graph there only adds encode, compression, transfer, and parse work.
    request.write_json(session_metadata_route_payload(request, parsed))


def get_tmux_session_exists(request: Any, parsed: Any, route: Route) -> None:
    del route
    qs = request_query(request, parsed)
    session = session_param(qs)
    request.write_app_result(request.server.app.tmux_session_exists_payload(session))


def get_agent_auth(request: Any, parsed: Any, route: Route) -> None:
    del route
    qs = request_query(request, parsed)
    request.write_json(request.server.app.agent_auth_payload(force=query_bool(qs, "force")))


def get_activity_summary(request: Any, parsed: Any, route: Route) -> None:
    del route
    if not activity_summary_enabled():
        metadata, body = activity_summary_disabled_response()
        request.write_json_bytes(body, status=HTTPStatus(int(metadata["status"])))
        return
    qs = request_query(request, parsed)
    body, status = request.server.app.activity_summary_bytes(
        force=query_bool(qs, "force"),
        locale=str(query_one(qs, "locale", "en") or "en"),
        session_scope=query_one(qs, "scope", "configured"),
        hours=query_one(qs, "hours", "24"),
    )
    request.write_json_bytes(body, status=status)


def get_background_status(request: Any, parsed: Any, route: Route) -> None:
    del parsed, route
    request.write_app_result(request.server.app.background_owner_status_payload())


def get_performance_diagnostics(request: Any, parsed: Any, route: Route) -> None:
    del route
    scope = str(query_one(request_query(request, parsed), "measurement_scope", "") or "")
    if scope not in {"", "capture"}:
        request.write_json({"ok": False, "error": "unsupported measurement scope"}, status=HTTPStatus.BAD_REQUEST)
        return
    if scope:
        request.write_json({"perf": request.server.app.performance_metrics_payload(measurement_scope=scope)})
        return
    request.write_json(request.server.app.performance_diagnostics_payload())


def get_system_status(request: Any, parsed: Any, route: Route) -> None:
    """Serve the published system-status body. This handler assembles nothing.

    It reads one already-encoded snapshot and writes it through the shared opaque-product writer,
    so the request thread does no collection, no `json.dumps`, and none of the response envelope's
    `copy.deepcopy` of a ~70 KB body. When no current snapshot exists the same call returns an
    explicitly typed unavailable/stale body instead of rebuilding on this thread.
    """

    del parsed, route
    body, product = request.server.app.system_status_snapshot_response()
    request.write_product_bytes(body, product)


def get_system_status_advanced(request: Any, parsed: Any, route: Route) -> None:
    """Serve the separately retained Advanced-diagnostics body, on the same read-only terms."""

    del parsed, route
    body, product = request.server.app.system_status_snapshot_response(advanced=True)
    request.write_product_bytes(body, product)


def get_server_logs(request: Any, parsed: Any, route: Route) -> None:
    del parsed, route
    request.write_json(server_logs_payload())


def post_background_claim(request: Any, parsed: Any, route: Route) -> None:
    del parsed, route
    request.write_app_result(request.server.app.background_owner_claim_payload())


def get_yoagent_skills(request: Any, parsed: Any, route: Route) -> None:
    del parsed, route
    request.write_json(request.server.app.yoagent_skills_payload())


def get_yoagent_skill_files(request: Any, parsed: Any, route: Route) -> None:
    del route
    qs = request_query(request, parsed)
    payload, status = request.server.app.yoagent_skill_files_payload(
        str(query_one(qs, "kind", "") or ""),
        str(query_one(qs, "name", "") or ""),
    )
    request.write_json(payload, status=status)


def get_yoagent_conversation(request: Any, parsed: Any, route: Route) -> None:
    del parsed, route
    request.write_json(request.server.app.yoagent_conversation_payload())


def get_yoagent_jobs(request: Any, parsed: Any, route: Route) -> None:
    del parsed, route
    response, status = request.server.app.yoagent_controller.yoagent_jobs_payload()
    request.write_json(response, status=status)


def get_tmux(request: Any, parsed: Any, route: Route) -> None:
    del route
    request.write_int_query_app_result(
        parsed,
        "lines",
        90,
        MAX_TRANSCRIPT_TAIL_LINES,
        lambda qs, lines: request.server.app.tmux_snapshot(session_param(qs), lines),
    )


def get_tmux_signals(request: Any, parsed: Any, route: Route) -> None:
    del route
    qs = request_query(request, parsed)
    request.write_app_result(request.server.app.tmux_signals_payload(force=query_bool(qs, "force"), session=session_param(qs)))

def get_tmux_status(request: Any, parsed: Any, route: Route) -> None:
    del route
    qs = request_query(request, parsed)
    request.write_app_result(request.server.app.tmux_status_mode(session_param(qs)))


def get_transcript(request: Any, parsed: Any, route: Route) -> None:
    del route
    request.write_int_query_app_result(
        parsed,
        "lines",
        120,
        MAX_TRANSCRIPT_TAIL_LINES,
        lambda qs, lines: request.server.app.transcript_tail(session_param(qs), lines),
    )


def get_context(request: Any, parsed: Any, route: Route) -> None:
    del route
    request.write_int_query_app_result(
        parsed,
        "messages",
        40,
        MAX_COMPACT_TRANSCRIPT_ITEMS,
        lambda qs, messages: request.server.app.context_tail(session_param(qs), messages),
    )


def get_context_items(request: Any, parsed: Any, route: Route) -> None:
    del route
    request.write_int_query_app_result(
        parsed,
        "messages",
        40,
        MAX_COMPACT_TRANSCRIPT_ITEMS,
        lambda qs, messages: request.server.app.context_items(session_param(qs), messages),
    )


def get_context_stream(request: Any, parsed: Any, route: Route) -> None:
    del route
    request.stream_context_items(parsed)


def get_summary_stream(request: Any, parsed: Any, route: Route) -> None:
    del route
    request.stream_codex_summary(parsed)


def get_auto_approve(request: Any, parsed: Any, route: Route) -> None:
    del route
    qs = request_query(request, parsed)
    session = session_param(qs, None)
    body, status = request.server.app.auto_approve_status_bytes(session)
    if status == HTTPStatus.OK:
        request.write_product_bytes(body, inline_json_product_metadata(body))
    else:
        request.write_json_bytes(body, status=status)


def get_notify(request: Any, parsed: Any, route: Route) -> None:
    del parsed, route
    request.write_json(request.server.app.notify_status())


def get_settings(request: Any, parsed: Any, route: Route) -> None:
    del parsed, route
    payload = request.server.app.settings_payload()
    if _write_settings_load_failure(request, payload):
        return
    request.write_json(payload)


def _write_settings_load_failure(request: Any, payload: Any | None = None) -> bool:
    """Surface an unreadable settings file before a route can use default-looking data."""

    settings_payload = payload if isinstance(payload, dict) else request.server.app.settings_payload()
    error = settings_payload.get("error") if isinstance(settings_payload, dict) else "settings payload unavailable"
    if not isinstance(error, str) or not error.strip():
        return False
    request.write_json(
        {
            **error_payload(
                error,
                message_key="common.requestFailed",
                status=HTTPStatus.SERVICE_UNAVAILABLE,
                reason=error,
            ),
            "code": "settings_file_malformed",
            "settings": None,
            "terminal": True,
        },
        status=HTTPStatus.SERVICE_UNAVAILABLE,
    )
    return True


def get_watched_prs(request: Any, parsed: Any, route: Route) -> None:
    del parsed, route
    request.write_json(request.server.app.watched_prs_payload())


def get_yolo_rules(request: Any, parsed: Any, route: Route) -> None:
    del parsed, route
    request.write_json(request.server.app.yolo_rules_payload())


def get_events(request: Any, parsed: Any, route: Route) -> None:
    del route
    request.write_int_query_app_result(
        parsed,
        "limit",
        100,
        MAX_EVENT_TAIL_LINES,
        lambda qs, limit: request.server.app.events_payload(session_param(qs, None), limit),
    )


def get_search(request: Any, parsed: Any, route: Route) -> None:
    del route
    request.write_int_query_app_result(
        parsed,
        "limit",
        100,
        MAX_EVENT_TAIL_LINES,
        lambda qs, limit: request.server.app.search_payload(str(query_one(qs, "q", "") or ""), session_param(qs, None), limit),
    )


def get_run_history(request: Any, parsed: Any, route: Route) -> None:
    del route
    qs = request_query(request, parsed)
    session = session_param(qs, None)
    request.write_app_result(request.server.app.run_history_payload(session))


def get_activity(request: Any, parsed: Any, route: Route) -> None:
    del route
    qs = request_query(request, parsed)
    visible = query_bool(qs, "visible", True)
    request.write_validated_float_result(
        qs,
        "hours",
        24.0,
        ACTIVITY_MAX_HOURS,
        lambda hours: request.server.app.activity_payload(hours=hours, visible=visible),
    )


def get_session_files_batch(request: Any, parsed: Any, route: Route) -> None:
    del route
    qs = request_query(request, parsed)
    requested_sessions = query_list(qs, "session") or query_list(qs, "sessions")
    from_ref = query_one(qs, "from", None)
    to_ref = query_one(qs, "to", None)
    force = query_bool(qs, "force")
    repo_refs = parse_repo_refs_param(query_one(qs, "refs", None))

    def make_result(hours: float) -> tuple[Any, HTTPStatus]:
        return request.server.app.session_files_batch_payload(requested_sessions or None, hours, from_ref=from_ref, to_ref=to_ref, repo_refs=repo_refs, force=force)

    request.write_validated_float_result(qs, "hours", 24.0, ACTIVITY_MAX_HOURS, make_result)


def get_session_files(request: Any, parsed: Any, route: Route) -> None:
    del route
    qs = request_query(request, parsed)
    session = session_param(qs, None)
    from_ref = query_one(qs, "from", None)
    to_ref = query_one(qs, "to", None)
    force = query_bool(qs, "force")
    repo_refs = parse_repo_refs_param(query_one(qs, "refs", None))

    def make_result(hours: float) -> tuple[Any, HTTPStatus]:
        return request.server.app.session_files_http_payload(session, hours, from_ref=from_ref, to_ref=to_ref, repo_refs=repo_refs, force=force)

    request.write_validated_float_result(
        qs,
        "hours",
        24.0,
        ACTIVITY_MAX_HOURS,
        make_result,
    )


def get_summary(request: Any, parsed: Any, route: Route) -> None:
    del route
    qs = request_query(request, parsed)
    session = session_param(qs)
    request.write_app_result(request.server.app.summary(session))


def _chat_write_result(request: Any, operation: Callable[[], dict[str, Any]], *, created: bool = False) -> None:
    if _write_settings_load_failure(request):
        return
    try:
        payload = operation()
    except ChatServiceError as error:
        request.write_json(
            error_payload(str(error), message_key="common.requestFailed", status=error.status, code=error.code),
            status=error.status,
        )
        return
    except (ChatStoreValidationError, TypeError, ValueError) as error:
        request.write_json(
            error_payload(str(error), message_key="common.requestFailed", status=HTTPStatus.BAD_REQUEST, code="invalid"),
            status=HTTPStatus.BAD_REQUEST,
        )
        return
    request.write_json(payload, status=HTTPStatus.CREATED if created and payload.get("created") else HTTPStatus.OK)


def get_chat_bootstrap(request: Any, parsed: Any, route: Route) -> None:
    del route
    qs = request_query(request, parsed)
    identity = request.auth_identity()
    request_ip = client_ip(request)

    def bootstrap_payload() -> dict[str, Any]:
        payload = request.server.app.chat_bootstrap(
            identity.username,
            str(query_one(qs, "browser_instance_id", "") or ""),
        )
        payload["client_ip"] = request_ip
        return payload

    _chat_write_result(
        request,
        bootstrap_payload,
    )


def get_chat_page(request: Any, parsed: Any, route: Route) -> None:
    del route
    qs = request_query(request, parsed)
    _chat_write_result(
        request,
        lambda: request.server.app.chat_page(
            request.auth_identity().username,
            before=str(query_one(qs, "before", "") or ""),
            limit=str(query_one(qs, "limit", "50") or "50"),
        ),
    )


def get_chat_delta(request: Any, parsed: Any, route: Route) -> None:
    del route
    qs = request_query(request, parsed)
    _chat_write_result(
        request,
        lambda: request.server.app.chat_delta(
            request.auth_identity().username,
            after=str(query_one(qs, "after", "") or ""),
            limit=str(query_one(qs, "limit", "200") or "200"),
        ),
    )


def get_chat_context(request: Any, parsed: Any, route: Route) -> None:
    del route
    qs = request_query(request, parsed)
    _chat_write_result(
        request,
        lambda: request.server.app.chat_context(
            request.auth_identity().username,
            message_id=str(query_one(qs, "message_id", "") or ""),
            before=str(query_one(qs, "before", "3") or "3"),
            after=str(query_one(qs, "after", "3") or "3"),
        ),
    )


def get_chat_search(request: Any, parsed: Any, route: Route) -> None:
    del route
    qs = request_query(request, parsed)
    _chat_write_result(
        request,
        lambda: request.server.app.chat_search(
            request.auth_identity().username,
            query=str(query_one(qs, "query", "") or ""),
            cursor=str(query_one(qs, "cursor", "") or ""),
            limit=str(query_one(qs, "limit", "20") or "20"),
        ),
    )


def post_chat_send(request: Any, parsed: Any, route: Route) -> None:
    del parsed
    payload = require_json_body(request, route)
    if payload is None:
        return
    identity = request.auth_identity()
    _chat_write_result(
        request,
        lambda: request.server.app.chat_send(
            identity.username,
            payload,
            request.request_locale_pref(),
            sender_ip=client_ip(request),
        ),
        created=True,
    )


def post_chat_yoagent(request: Any, parsed: Any, route: Route) -> None:
    del parsed
    payload = require_json_body(request, route)
    if payload is None:
        return
    identity = request.auth_identity()
    _chat_write_result(
        request,
        lambda: request.server.app.chat_yoagent(
            identity.username,
            identity.role,
            payload,
            request.request_locale_pref(),
        ),
        created=True,
    )


def post_chat_typing(request: Any, parsed: Any, route: Route) -> None:
    del parsed
    payload = require_json_body(request, route)
    if payload is None:
        return
    _chat_write_result(
        request,
        lambda: request.server.app.chat_typing(
            request.auth_identity().username,
            payload.get("browser_instance_id"),
            payload.get("typing") is True,
        ),
    )


def post_chat_read(request: Any, parsed: Any, route: Route) -> None:
    del parsed
    payload = require_json_body(request, route)
    if payload is None:
        return
    _chat_write_result(
        request,
        lambda: request.server.app.chat_read(
            request.auth_identity().username,
            payload.get("message_id"),
        ),
    )


def get_fs_list(request: Any, parsed: Any, route: Route) -> None:
    del route
    request.handle_fs_list(parsed)


def get_fs_fast_list(request: Any, parsed: Any, route: Route) -> None:
    del route
    request.handle_fs_fast_list(parsed)


def get_fs_search(request: Any, parsed: Any, route: Route) -> None:
    del route
    request.handle_fs_search(parsed)


def get_fs_index_status(request: Any, parsed: Any, route: Route) -> None:
    del route
    request.handle_fs_index_status(parsed)


def get_fs_read(request: Any, parsed: Any, route: Route) -> None:
    del route
    request.handle_fs_read(parsed)


def get_fs_info(request: Any, parsed: Any, route: Route) -> None:
    del route
    request.handle_fs_info(parsed)


def get_fs_diff(request: Any, parsed: Any, route: Route) -> None:
    del route
    request.handle_fs_diff(parsed)


def get_fs_git_history(request: Any, parsed: Any, route: Route) -> None:
    del route
    request.handle_fs_git_history(parsed)


def get_fs_git_commit(request: Any, parsed: Any, route: Route) -> None:
    del route
    request.handle_fs_git_commit(parsed)


def get_fs_watch_diff(request: Any, parsed: Any, route: Route) -> None:
    del route
    qs = request_query(request, parsed)
    payload, status = request.server.app.filesystem_watch_diff_http_payload(
        since_token=str(query_one(qs, "since", "") or ""),
        force_full=query_bool(qs, "full"),
        request_id=request.api_request_id(),
    )
    request.write_json(payload, status=status)


def get_blame(request: Any, parsed: Any, route: Route) -> None:
    del route
    request.handle_blame(parsed)


def get_fs_raw(request: Any, parsed: Any, route: Route) -> None:
    del route
    request.handle_fs_raw(parsed)


def get_fs_zip(request: Any, parsed: Any, route: Route) -> None:
    del route
    request.handle_fs_zip(parsed)


def get_fs_count(request: Any, parsed: Any, route: Route) -> None:
    del route
    request.handle_fs_count(parsed)


def get_fs_html_preview(request: Any, parsed: Any, route: Route) -> None:
    del route
    request.handle_fs_html_preview(parsed)


def get_websocket(request: Any, parsed: Any, route: Route) -> None:
    del route
    request.websocket(parsed)


def post_login(request: Any, parsed: Any, route: Route) -> None:
    del route
    request.handle_login_submit(parsed)


def post_self_update(request: Any, parsed: Any, route: Route) -> None:
    del route
    if request.auth_readonly():
        request.reject_forbidden(request.auth_identity(), "admin")
        return
    request.write_json(request.server.app.perform_self_update(dryrun=query_bool(request_query(request, parsed), "dryrun")))


def post_ensure_session(request: Any, parsed: Any, route: Route) -> None:
    del route
    qs = request_query(request, parsed)
    session = session_param(qs)
    request.write_app_result(request.server.app.ensure_session(session))


def post_create_session(request: Any, parsed: Any, route: Route) -> None:
    del route
    qs = request_query(request, parsed)
    agent = str(query_one(qs, "agent", "claude") or "claude")
    dangerously_yolo = query_bool(qs, "dangerously_yolo", request.server.app.dangerously_yolo)
    terminal = str(query_one(qs, "terminal", "") or "")
    session = str(query_one(qs, "session", "") or "")
    generation, error = parse_query_int(qs, "generation", 0, min_value=0)
    if error:
        request.write_json(error.payload(), status=HTTPStatus.BAD_REQUEST)
        return
    request.write_app_result(request.server.app.create_next_session(agent, dangerously_yolo, terminal, session, generation))


def get_create_session_plan(request: Any, parsed: Any, route: Route) -> None:
    del parsed, route
    request.write_app_result(request.server.app.create_next_session_plan())


def post_rename_session(request: Any, parsed: Any, route: Route) -> None:
    del route
    qs = request_query(request, parsed)
    session = session_param(qs)
    new_name = str(query_one(qs, "new_name", "") or "")
    request.write_app_result(request.server.app.rename_session(session, new_name))


def post_kill_session(request: Any, parsed: Any, route: Route) -> None:
    del route
    qs = request_query(request, parsed)
    session = session_param(qs)
    request.write_app_result(request.server.app.kill_session(session))


def post_upload(request: Any, parsed: Any, route: Route) -> None:
    del route
    qs = request_query(request, parsed)
    session = session_param(qs)
    editor_path = str(query_one(qs, "editor_path", "") or "")
    base_dir = str(query_one(qs, "base_dir", "") or "")
    request.write_app_result(request.handle_upload(session, editor_path=editor_path, base_dir=base_dir))


def post_auto_approve(request: Any, parsed: Any, route: Route) -> None:
    del route
    qs = request_query(request, parsed)
    session = session_param(qs)
    enabled = query_bool(qs, "enabled")
    request.write_app_result(request.server.app.set_auto_approve(session, enabled))


def post_attention_ack(request: Any, parsed: Any, route: Route) -> None:
    del parsed
    payload = require_json_body(request, route)
    if payload is None:
        return
    request.write_app_result(request.server.app.acknowledge_attention(payload))


def post_notify(request: Any, parsed: Any, route: Route) -> None:
    del route
    qs = request_query(request, parsed)
    enabled = query_bool(qs, "enabled")
    request.write_json(request.server.app.set_notify(enabled))


def post_settings(request: Any, parsed: Any, route: Route) -> None:
    del parsed
    payload = require_json_body(request, route)
    if payload is None:
        return
    request.write_json(request.server.app.save_settings(payload.get("settings", payload)))


def post_watch_roots(request: Any, parsed: Any, route: Route) -> None:
    del parsed
    payload = require_json_body(request, route)
    if payload is None:
        return
    try:
        response = request.server.app.update_client_watch_roots(payload)
    except ClientWatchRootValidationError as error:
        request.write_json(
            error_payload(
                str(error),
                message_key="common.requestFailed",
                canonical=True,
                code="invalid_request",
                origin="server.http",
                retryable=False,
                details={},
                stack=[{
                    "component": "server.http",
                    "operation": "POST /api/watch/roots",
                    "code": "invalid_request",
                }],
            ),
            status=HTTPStatus.BAD_REQUEST,
        )
        return
    request.write_json(response)


def post_drop_action(request: Any, parsed: Any, route: Route) -> None:
    del parsed
    payload = require_json_body(request, route)
    if payload is None:
        return
    request.write_app_result(request.server.app.run_file_drop_action(payload))


def post_yoagent_chat(request: Any, parsed: Any, route: Route) -> None:
    del parsed
    payload = require_json_body(request, route)
    if payload is None:
        return
    response, status = request.server.app.yoagent_controller.yoagent_chat(payload, access_role=request.auth_identity().role)
    request.write_json(response, status=status)


def post_yoagent_chat_cancel(request: Any, parsed: Any, route: Route) -> None:
    payload = require_json_body(request, route)
    if payload is None:
        return
    request_id = unquote(parsed.path[len("/api/yoagent/chat/"):-len("/cancel")]).strip("/")
    response, status = request.server.app.yoagent_controller.cancel_yoagent_chat(str(payload.get("request_id") or request_id))
    request.write_json(response, status=status)


def post_yoagent_preview_send(request: Any, parsed: Any, route: Route) -> None:
    del parsed
    payload = require_json_body(request, route)
    if payload is None:
        return
    response, status = request.server.app.yoagent_controller.preview_yoagent_send_action(payload)
    request.write_json(response, status=status)


def post_yoagent_execute_send(request: Any, parsed: Any, route: Route) -> None:
    del parsed
    payload = require_json_body(request, route)
    if payload is None:
        return
    response, status = request.server.app.yoagent_controller.execute_yoagent_send_action(payload)
    request.write_json(response, status=status)


def post_yoagent_intent(request: Any, parsed: Any, route: Route) -> None:
    del parsed
    payload = require_json_body(request, route)
    if payload is None:
        return
    response, status = request.server.app.yoagent_controller.yoagent_intent(payload)
    request.write_json(response, status=status)


def post_yoagent_jobs(request: Any, parsed: Any, route: Route) -> None:
    del parsed
    payload = require_json_body(request, route)
    if payload is None:
        return
    response, status = request.server.app.yoagent_controller.create_yoagent_job(payload)
    request.write_json(response, status=status)


def post_yoagent_job_confirm(request: Any, parsed: Any, route: Route) -> None:
    payload = require_json_body(request, route)
    if payload is None:
        return
    job_id = unquote(parsed.path[len("/api/yoagent/jobs/"):-len("/confirm")]).strip("/")
    response, status = request.server.app.yoagent_controller.confirm_yoagent_job(str(payload.get("id") or job_id))
    request.write_json(response, status=status)


def post_yoagent_job_cancel(request: Any, parsed: Any, route: Route) -> None:
    payload = require_json_body(request, route)
    if payload is None:
        return
    job_id = unquote(parsed.path[len("/api/yoagent/jobs/"):-len("/cancel")]).strip("/")
    response, status = request.server.app.yoagent_controller.cancel_yoagent_job(str(payload.get("id") or job_id))
    request.write_json(response, status=status)


def post_yoagent_jobs_cancel_session(request: Any, parsed: Any, route: Route) -> None:
    del parsed
    payload = require_json_body(request, route)
    if payload is None:
        return
    response, status = request.server.app.yoagent_controller.cancel_yoagent_jobs_for_session(str(payload.get("session") or ""))
    request.write_json(response, status=status)


def post_yoagent_wait_clear(request: Any, parsed: Any, route: Route) -> None:
    payload = require_json_body(request, route)
    if payload is None:
        return
    wait_id = unquote(parsed.path[len("/api/yoagent/waits/"):-len("/clear")]).strip("/")
    response, status = request.server.app.yoagent_controller.clear_yoagent_action_wait(str(payload.get("id") or wait_id))
    request.write_json(response, status=status)


def post_yoagent_skill_file_upsert(request: Any, parsed: Any, route: Route) -> None:
    del parsed
    payload = require_json_body(request, route)
    if payload is None:
        return
    response, status = request.server.app.upsert_yoagent_skill_file(payload)
    request.write_json(response, status=status)


def post_yoagent_skill_file_delete(request: Any, parsed: Any, route: Route) -> None:
    del parsed
    payload = require_json_body(request, route)
    if payload is None:
        return
    response, status = request.server.app.delete_yoagent_skill_file(payload)
    request.write_json(response, status=status)


def post_yoagent_prewarm(request: Any, parsed: Any, route: Route) -> None:
    del parsed
    payload = require_json_body(request, route)
    if payload is None:
        return
    response, status = request.server.app.yoagent_controller.yoagent_prewarm(payload)
    request.write_json(response, status=status)


def post_yoagent_reset(request: Any, parsed: Any, route: Route) -> None:
    del parsed, route
    request.write_json(request.server.app.yoagent_controller.reset_yoagent_chat())


def post_yolo_rules_reload(request: Any, parsed: Any, route: Route) -> None:
    del parsed, route
    request.write_json(request.server.app.reload_yolo_rules())


def post_yolo_rules_open(request: Any, parsed: Any, route: Route) -> None:
    del parsed, route
    request.write_json(request.server.app.ensure_yolo_rules_file())


def post_tmux_next(request: Any, parsed: Any, route: Route) -> None:
    del route
    qs = request_query(request, parsed)
    session = session_param(qs)
    request.write_app_result(request.server.app.tmux_next_window(session))

def post_tmux_status(request: Any, parsed: Any, route: Route) -> None:
    del route
    qs = request_query(request, parsed)
    request.write_app_result(request.server.app.cycle_tmux_status_mode(session_param(qs)))


def post_tmux_window(request: Any, parsed: Any, route: Route) -> None:
    del route
    qs = request_query(request, parsed)
    session = session_param(qs)
    window = qs.get("window", [""])[0]
    payload, status = request.server.app.tmux_select_window(session, window)
    request.write_json(payload, status=status)


def post_tmux_copy_selection(request: Any, parsed: Any, route: Route) -> None:
    del route
    qs = request_query(request, parsed)
    session = session_param(qs)
    request.write_app_result(request.server.app.tmux_copy_selection(session))


def post_event(request: Any, parsed: Any, route: Route) -> None:
    del parsed, route
    request.write_app_result(request.handle_client_event())


def post_fs_batch(request: Any, parsed: Any, route: Route) -> None:
    del route
    request.handle_fs_batch(parsed)


def post_fs_write(request: Any, parsed: Any, route: Route) -> None:
    del route
    request.handle_fs_write(parsed)


def post_fs_delete(request: Any, parsed: Any, route: Route) -> None:
    del route
    request.handle_fs_delete(parsed)


def post_fs_unindex(request: Any, parsed: Any, route: Route) -> None:
    del route
    request.handle_fs_unindex(parsed)


def post_fs_rename(request: Any, parsed: Any, route: Route) -> None:
    del route
    request.handle_fs_rename(parsed)


def post_fs_mkdir(request: Any, parsed: Any, route: Route) -> None:
    del route
    request.handle_fs_mkdir(parsed)


CORE_ROUTES = (
    Route("GET", "/static/*", PUBLIC, get_static_asset, protocol=RESPONSE_STATIC, group="core"),
    Route("GET", "/api/auth-setup", PUBLIC, get_auth_setup, protocol=RESPONSE_JSON, group="core"),
    Route("GET", "/login", PUBLIC, get_login, protocol=RESPONSE_HTML, group="core"),
    Route("GET", "/logout", PUBLIC, get_logout, protocol=RESPONSE_REDIRECT, group="core"),
    Route("GET", "/healthz", PUBLIC, get_healthz, protocol=RESPONSE_JSON, group="core"),
    # AUTHENTICATED. `/readyz` reports pending cell counts, migration state and failure
    # strings -- internal operational detail about a running daemon, which must not be
    # world-readable. The role is enforced by `_dispatch_route_handler` like every other
    # authenticated route here; there is deliberately no second auth check in the handler.
    Route("GET", "/readyz", "readonly", get_readyz, protocol=RESPONSE_JSON, group="core"),
    Route("GET", "/livez", PUBLIC, get_livez, protocol=RESPONSE_JSON, group="core"),
    Route("GET", "/api/ping", "readonly", get_ping, protocol=RESPONSE_JSON, group="core"),
    Route("GET", "/api/stats-capabilities", "readonly", get_stats_capabilities, protocol=RESPONSE_JSON, group="core", normal_session_local_service=True),
    Route("GET", "/api/stats-delta", "readonly", get_stats_delta, protocol=RESPONSE_JSON, group="core", normal_session_local_service=True),
    Route("GET", "/api/stats-snapshot", "readonly", get_stats_snapshot, protocol=RESPONSE_JSON, group="core", normal_session_local_service=True),
    Route("GET", "/api/stats-stream", "readonly", get_stats_stream, protocol=RESPONSE_SSE, group="core"),
    Route("POST", "/api/stats-retry", "readonly", post_stats_retry, protocol=RESPONSE_JSON, group="core"),
    Route("GET", "/api/pricing-catalog", "readonly", get_pricing_catalog, protocol=RESPONSE_JSON, group="core"),
    Route("GET", "/api/update-status", "admin", get_update_status, protocol=RESPONSE_JSON, group="core"),
    Route("GET", "/api/dev-reload", "readonly", get_dev_reload, protocol=RESPONSE_SSE, group="core"),
    Route("GET", "/api/client-events", "readonly", get_client_events, protocol=RESPONSE_SSE, group="core"),
    Route("GET", "/api/operations/*", "readonly", get_operation, protocol=RESPONSE_JSON, group="core"),
    Route("POST", "/api/operations/ack", "readonly", post_operation_acknowledgments, protocol=RESPONSE_JSON, body_limit=16 * 1024, group="core"),
    Route("GET", "/", "readonly", get_home, protocol=RESPONSE_HTML, group="core"),
    Route("GET", "/preview-popout", "readonly", get_preview_popout, protocol=RESPONSE_HTML, group="core"),
    Route("GET", "/pane-popout", "readonly", get_pane_popout, protocol=RESPONSE_HTML, group="core"),
    Route("GET", "/api/session-metadata", "readonly", get_session_metadata, protocol=RESPONSE_JSON, group="core", normal_session_local_service=True),
    Route("GET", "/api/transcripts", "readonly", get_transcripts, protocol=RESPONSE_JSON, group="core", normal_session_local_service=True),
    Route("GET", "/api/agent-auth", "readonly", get_agent_auth, protocol=RESPONSE_JSON, group="core"),
    Route("GET", "/api/activity-summary", "readonly", get_activity_summary, protocol=RESPONSE_JSON, group="core"),
    Route("GET", "/api/background/status", "readonly", get_background_status, protocol=RESPONSE_JSON, group="core"),
    Route("GET", "/api/system-status", "readonly", get_system_status, protocol=RESPONSE_JSON, group="core", normal_session_local_service=True),
    # Advanced diagnostics are a separate retained body, fetched when a reader opens the
    # disclosure rather than assembled into every five-second poll. It reads a published snapshot
    # only, so unlike the route above it never touches a normal-session local service.
    Route("GET", "/api/system-status/advanced", "readonly", get_system_status_advanced, protocol=RESPONSE_JSON, group="core"),
    Route("GET", "/api/logs", "readonly", get_server_logs, protocol=RESPONSE_JSON, group="core"),
    Route("GET", "/api/diagnostics/performance", "admin", get_performance_diagnostics, protocol=RESPONSE_JSON, group="core"),
    Route("GET", "/api/auto-approve", "readonly", get_auto_approve, protocol=RESPONSE_JSON, group="core", normal_session_local_service=True),
    Route("GET", "/api/notify", "readonly", get_notify, protocol=RESPONSE_JSON, group="core"),
    Route("GET", "/api/settings", "readonly", get_settings, protocol=RESPONSE_JSON, group="core"),
    Route("GET", "/api/watched-prs", "readonly", get_watched_prs, protocol=RESPONSE_JSON, group="core"),
    Route("GET", "/api/yolo-rules", "readonly", get_yolo_rules, protocol=RESPONSE_JSON, group="core"),
    Route("GET", "/api/events", "readonly", get_events, protocol=RESPONSE_JSON, group="core"),
    Route("GET", "/api/search", "readonly", get_search, protocol=RESPONSE_JSON, group="core", normal_session_local_service=True),
    Route("GET", "/api/run-history", "readonly", get_run_history, protocol=RESPONSE_JSON, group="core", normal_session_local_service=True),
    Route("GET", "/api/activity", "readonly", get_activity, protocol=RESPONSE_JSON, group="core", normal_session_local_service=True),
    Route("GET", "/api/session-files-batch", "readonly", get_session_files_batch, protocol=RESPONSE_JSON, group="core", normal_session_local_service=True),
    Route("GET", "/api/session-files", "readonly", get_session_files, protocol=RESPONSE_JSON, group="core", normal_session_local_service=True),
    Route("GET", "/api/summary", "readonly", get_summary, protocol=RESPONSE_JSON, group="core", normal_session_local_service=True),
    Route("GET", "/api/tmux-session-exists", "readonly", get_tmux_session_exists, protocol=RESPONSE_JSON, group="core"),
    Route("POST", "/login", PUBLIC, post_login, protocol=RESPONSE_REDIRECT, group="core"),
    Route("POST", "/api/self-update", "admin", post_self_update, protocol=RESPONSE_JSON, group="core"),
    Route("POST", "/api/stats-observations", "readonly", post_stats_observations, protocol=RESPONSE_JSON, body_limit=128 * 1024, group="core"),
    Route("POST", "/api/pricing-catalog/refresh", "admin", post_pricing_catalog_refresh, protocol=RESPONSE_JSON, group="core"),
    Route("POST", "/api/background/claim", "admin", post_background_claim, protocol=RESPONSE_JSON, group="core"),
    Route("POST", "/api/ensure-session", "admin", post_ensure_session, protocol=RESPONSE_JSON, group="core"),
    Route("GET", "/api/create-session-plan", "admin", get_create_session_plan, protocol=RESPONSE_JSON, group="core"),
    Route("POST", "/api/create-session", "admin", post_create_session, protocol=RESPONSE_JSON, group="core"),
    Route("POST", "/api/rename-session", "admin", post_rename_session, protocol=RESPONSE_JSON, group="core"),
    Route("POST", "/api/kill-session", "admin", post_kill_session, protocol=RESPONSE_JSON, group="core"),
    Route("POST", "/api/upload", "admin", post_upload, protocol=RESPONSE_JSON, group="core"),
    Route("POST", "/api/auto-approve", "admin", post_auto_approve, protocol=RESPONSE_JSON, group="core"),
    Route("POST", "/api/attention-ack", "admin", post_attention_ack, protocol=RESPONSE_JSON, body_limit=16 * 1024, group="core"),
    Route("POST", "/api/notify", "admin", post_notify, protocol=RESPONSE_JSON, group="core"),
    Route("POST", "/api/settings", "admin", post_settings, protocol=RESPONSE_JSON, body_limit=64 * 1024, group="core"),
    Route("POST", "/api/watch/roots", "admin", post_watch_roots, protocol=RESPONSE_JSON, body_limit=64 * 1024, group="core"),
    Route("POST", "/api/drop-action/run", "admin", post_drop_action, protocol=RESPONSE_JSON, body_limit=64 * 1024, group="core"),
    Route("POST", "/api/yolo-rules/reload", "admin", post_yolo_rules_reload, protocol=RESPONSE_JSON, group="core"),
    Route("POST", "/api/yolo-rules/open", "admin", post_yolo_rules_open, protocol=RESPONSE_JSON, group="core"),
    Route("POST", "/api/event", "readonly", post_event, protocol=RESPONSE_JSON, group="core"),
)

YOAGENT_ROUTES = (
    Route("GET", "/api/yoagent/skills", "admin", get_yoagent_skills, protocol=RESPONSE_JSON, group="yoagent"),
    Route("GET", "/api/yoagent/skill-files", "admin", get_yoagent_skill_files, protocol=RESPONSE_JSON, group="yoagent"),
    Route("GET", "/api/yoagent/conversation", "admin", get_yoagent_conversation, protocol=RESPONSE_JSON, group="yoagent"),
    Route("GET", "/api/yoagent/jobs", "admin", get_yoagent_jobs, protocol=RESPONSE_JSON, group="yoagent"),
    Route("POST", "/api/yoagent/chat", "readonly", post_yoagent_chat, protocol=RESPONSE_JSON, body_limit=64 * 1024, group="yoagent"),
    Route("POST", "/api/yoagent/chat/*/cancel", "admin", post_yoagent_chat_cancel, protocol=RESPONSE_JSON, body_limit=16 * 1024, group="yoagent"),
    Route("POST", "/api/yoagent/actions/preview-send", "admin", post_yoagent_preview_send, protocol=RESPONSE_JSON, body_limit=64 * 1024, group="yoagent"),
    Route("POST", "/api/yoagent/actions/execute-send", "admin", post_yoagent_execute_send, protocol=RESPONSE_JSON, body_limit=16 * 1024, group="yoagent"),
    Route("POST", "/api/yoagent/intent", "admin", post_yoagent_intent, protocol=RESPONSE_JSON, body_limit=64 * 1024, group="yoagent"),
    Route("POST", "/api/yoagent/jobs", "admin", post_yoagent_jobs, protocol=RESPONSE_JSON, body_limit=64 * 1024, group="yoagent"),
    Route("POST", "/api/yoagent/jobs/cancel-session", "admin", post_yoagent_jobs_cancel_session, protocol=RESPONSE_JSON, body_limit=16 * 1024, group="yoagent"),
    Route("POST", "/api/yoagent/jobs/*/confirm", "admin", post_yoagent_job_confirm, protocol=RESPONSE_JSON, body_limit=16 * 1024, group="yoagent"),
    Route("POST", "/api/yoagent/jobs/*/cancel", "admin", post_yoagent_job_cancel, protocol=RESPONSE_JSON, body_limit=16 * 1024, group="yoagent"),
    Route("POST", "/api/yoagent/waits/*/clear", "admin", post_yoagent_wait_clear, protocol=RESPONSE_JSON, body_limit=16 * 1024, group="yoagent"),
    Route("POST", "/api/yoagent/skill-files/upsert", "admin", post_yoagent_skill_file_upsert, protocol=RESPONSE_JSON, body_limit=64 * 1024, group="yoagent"),
    Route("POST", "/api/yoagent/skill-files/delete", "admin", post_yoagent_skill_file_delete, protocol=RESPONSE_JSON, body_limit=16 * 1024, group="yoagent"),
    Route("POST", "/api/yoagent/prewarm", "admin", post_yoagent_prewarm, protocol=RESPONSE_JSON, body_limit=64 * 1024, group="yoagent"),
    Route("POST", "/api/yoagent/reset", "admin", post_yoagent_reset, protocol=RESPONSE_JSON, group="yoagent"),
)

CHAT_ROUTES = (
    Route("GET", "/api/chat/bootstrap", "readonly", get_chat_bootstrap, protocol=RESPONSE_JSON, group="chat"),
    Route("GET", "/api/chat/page", "readonly", get_chat_page, protocol=RESPONSE_JSON, group="chat"),
    Route("GET", "/api/chat/delta", "readonly", get_chat_delta, protocol=RESPONSE_JSON, group="chat"),
    Route("GET", "/api/chat/context", "readonly", get_chat_context, protocol=RESPONSE_JSON, group="chat"),
    Route("GET", "/api/chat/search", "readonly", get_chat_search, protocol=RESPONSE_JSON, group="chat"),
    Route("POST", "/api/chat/send", "readonly", post_chat_send, protocol=RESPONSE_JSON, body_limit=12 * 1024, group="chat"),
    Route("POST", "/api/chat/yoagent", "readonly", post_chat_yoagent, protocol=RESPONSE_JSON, body_limit=4096, group="chat"),
    Route("POST", "/api/chat/typing", "readonly", post_chat_typing, protocol=RESPONSE_JSON, body_limit=4096, group="chat"),
    Route("POST", "/api/chat/read", "readonly", post_chat_read, protocol=RESPONSE_JSON, body_limit=4096, group="chat"),
)

FILESYSTEM_ROUTES = (
    Route("GET", "/api/fs/fast/list", "readonly", get_fs_fast_list, protocol=RESPONSE_JSON, group="filesystem"),
    Route("GET", "/api/fs/list", "readonly", get_fs_list, protocol=RESPONSE_JSON, group="filesystem"),
    Route("GET", "/api/fs/search", "readonly", get_fs_search, protocol=RESPONSE_JSON, group="filesystem"),
    Route("GET", "/api/fs/index-status", "readonly", get_fs_index_status, protocol=RESPONSE_JSON, group="filesystem"),
    Route("GET", "/api/fs/read", "readonly", get_fs_read, protocol=RESPONSE_JSON, group="filesystem"),
    Route("GET", "/api/fs/info", "readonly", get_fs_info, protocol=RESPONSE_JSON, group="filesystem"),
    Route("GET", "/api/fs/diff", "readonly", get_fs_diff, protocol=RESPONSE_JSON, group="filesystem"),
    Route("GET", "/api/fs/git-history", "readonly", get_fs_git_history, protocol=RESPONSE_JSON, group="filesystem"),
    Route("GET", "/api/fs/git-commit", "readonly", get_fs_git_commit, protocol=RESPONSE_JSON, group="filesystem"),
    Route("GET", "/api/fs/watch-diff", "readonly", get_fs_watch_diff, protocol=RESPONSE_JSON, group="filesystem"),
    Route("GET", "/api/blame", "readonly", get_blame, protocol=RESPONSE_JSON, group="filesystem"),
    Route("GET", "/api/fs/raw", "readonly", get_fs_raw, protocol=RESPONSE_BINARY, group="filesystem"),
    Route("GET", "/api/fs/zip", "readonly", get_fs_zip, protocol=RESPONSE_BINARY, group="filesystem"),
    Route("GET", "/api/fs/count", "readonly", get_fs_count, protocol=RESPONSE_JSON, group="filesystem"),
    Route("GET", "/api/fs/html-preview", "readonly", get_fs_html_preview, protocol=RESPONSE_BINARY, group="filesystem"),
    Route("POST", "/api/fs/batch", "admin", post_fs_batch, protocol=RESPONSE_JSON_BATCH, body_limit=64 * 1024, group="filesystem"),
    Route("POST", "/api/fs/write", "admin", post_fs_write, protocol=RESPONSE_JSON, group="filesystem"),
    Route("POST", "/api/fs/delete", "admin", post_fs_delete, protocol=RESPONSE_JSON, body_limit=4096, group="filesystem"),
    Route("POST", "/api/fs/unindex", "admin", post_fs_unindex, protocol=RESPONSE_JSON, body_limit=4096, group="filesystem"),
    Route("POST", "/api/fs/rename", "admin", post_fs_rename, protocol=RESPONSE_JSON, body_limit=4096, group="filesystem"),
    Route("POST", "/api/fs/mkdir", "admin", post_fs_mkdir, protocol=RESPONSE_JSON, body_limit=4096, group="filesystem"),
)

TMUX_ROUTES = (
    Route("GET", "/api/tmux", "readonly", get_tmux, protocol=RESPONSE_JSON, group="tmux"),
    Route("GET", "/api/tmux-signals", "readonly", get_tmux_signals, protocol=RESPONSE_JSON, group="tmux"),
    Route("GET", "/api/tmux-status", "readonly", get_tmux_status, protocol=RESPONSE_JSON, group="tmux"),
    Route("GET", "/api/transcript", "readonly", get_transcript, protocol=RESPONSE_JSON, group="tmux"),
    Route("GET", "/api/context", "readonly", get_context, protocol=RESPONSE_JSON, group="tmux", normal_session_local_service=True),
    Route("GET", "/api/context-items", "readonly", get_context_items, protocol=RESPONSE_JSON, group="tmux", normal_session_local_service=True),
    Route("GET", "/api/context-stream", "readonly", get_context_stream, protocol=RESPONSE_SSE, group="tmux"),
    Route("GET", "/api/summary-stream", "admin", get_summary_stream, protocol=RESPONSE_SSE, group="tmux"),
    Route("GET", "/ws", "readonly", get_websocket, protocol=RESPONSE_WEBSOCKET, group="tmux"),
    Route("POST", "/api/tmux-next", "admin", post_tmux_next, protocol=RESPONSE_JSON, group="tmux"),
    Route("POST", "/api/tmux-status", "admin", post_tmux_status, protocol=RESPONSE_JSON, group="tmux"),
    Route("POST", "/api/tmux-window", "admin", post_tmux_window, protocol=RESPONSE_JSON, group="tmux"),
    Route("POST", "/api/tmux-copy-selection", "admin", post_tmux_copy_selection, protocol=RESPONSE_JSON, group="tmux"),
)

ROUTE_GROUPS = {
    "core": CORE_ROUTES,
    "yoagent": YOAGENT_ROUTES,
    "chat": CHAT_ROUTES,
    "filesystem": FILESYSTEM_ROUTES,
    "tmux": TMUX_ROUTES,
}
ALL_ROUTES = tuple(route for routes in ROUTE_GROUPS.values() for route in routes)
ROUTES_BY_METHOD = {
    "GET": tuple(route for route in ALL_ROUTES if route.method == "GET"),
    "POST": tuple(route for route in ALL_ROUTES if route.method == "POST"),
}
