# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any
from urllib.parse import parse_qs
from urllib.parse import unquote
from urllib.parse import urlparse

from .common import ACTIVITY_MAX_HOURS
from .common import MAX_COMPACT_TRANSCRIPT_ITEMS
from .common import MAX_EVENT_TAIL_LINES
from .common import MAX_TRANSCRIPT_TAIL_LINES
from .common import SUMMARY_LOOKBACK_SECONDS
from .common import auth_setup_required
from .common import error_payload
from .common import parse_bool
from .chat_service import ChatServiceError
from .chat_store import ChatStoreValidationError
from .locales import resolve_locale_preference
from .web import html_page
from .web import server_string
from .web import static_content_type


RouteRole = str | Callable[[Any, Any], str]
RouteHandler = Callable[[Any, Any, "Route"], bool | None]
PUBLIC = "public"
SHARE_ACCESS_NONE = "none"
SHARE_ACCESS_READONLY = "readonly"
SHARE_ACCESS_SCOPED_FILE = "scoped-file"
SHARE_ACCESS_VALUES = frozenset({SHARE_ACCESS_NONE, SHARE_ACCESS_READONLY, SHARE_ACCESS_SCOPED_FILE})


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
    body_limit: int | None = None
    group: str = "core"
    share_access: str = SHARE_ACCESS_NONE

    def __post_init__(self) -> None:
        if self.share_access not in SHARE_ACCESS_VALUES:
            raise ValueError(f"invalid share access policy: {self.share_access}")


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


def parse_query_int(
    qs: dict[str, list[str]],
    name: str,
    default: int,
    *,
    min_value: int = 1,
    max_value: int | None = None,
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


def share_token_readonly_role(request: Any, parsed: Any) -> str:
    del parsed
    return "readonly" if request.share_token_text() else "admin"


def yoagent_chat_post_role(request: Any, parsed: Any) -> str:
    del parsed
    return "admin" if request.share_token_text() else "readonly"


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
    parsed = urlparse(request.path)
    if request.redirect_plaintext_to_https_if_needed(parsed):
        return

    route = route_for_request(method, parsed.path)
    if route is None:
        _write_not_found_after_default_auth(request, method)
        return

    if route.role == PUBLIC:
        handled = route.handler(request, parsed, route)
        if handled is False:
            _write_not_found_after_default_auth(request, method)
        return

    required_role = route_required_role(route, request, parsed)
    if required_role is not None and not request.require_auth(required_role):
        return
    if route.group == "filesystem" and request.auth_readonly() and not request.share_readonly_api_allowed(parsed):
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


def _json_body(request: Any, route: Route, *, allow_empty: bool = False, allow_missing: bool = False) -> dict[str, Any] | None:
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


def get_share_shell(request: Any, parsed: Any, route: Route) -> bool:
    del route
    return request.handle_share_shell(parsed)


def get_ping(request: Any, parsed: Any, route: Route) -> None:
    del parsed, route
    request.write_json({"ok": True, "time": time.time()})


def get_stats_sample(request: Any, parsed: Any, route: Route) -> None:
    del route
    started = time.perf_counter()
    qs = request_query(request, parsed)
    since, error = parse_query_int(qs, "since", 0, min_value=0)
    if error:
        request.write_json(error.payload(), status=HTTPStatus.BAD_REQUEST)
        return
    client_id = (qs.get("client_id") or qs.get("client") or [""])[0]
    token_consumer = query_bool(qs, "token_consumer") or query_bool(qs, "tokens")
    token_since, token_since_error = parse_query_int(qs, "token_since", 0, min_value=0)
    token_resolution, token_resolution_error = parse_query_int(qs, "token_resolution", 0, min_value=0)
    token_history_start, token_history_start_error = parse_query_int(qs, "token_history_start", 0, min_value=0)
    token_history_end, token_history_end_error = parse_query_int(qs, "token_history_end", 0, min_value=0)
    token_history_start_supplied = "token_history_start" in qs
    token_history_end_supplied = "token_history_end" in qs
    history_start, history_start_error = parse_query_int(qs, "history_start", 0, min_value=0)
    history_end, history_end_error = parse_query_int(qs, "history_end", 0, min_value=0)
    history_resolution, history_resolution_error = parse_query_int(qs, "history_resolution", 0, min_value=0, max_value=24 * 60 * 60)
    history_max_points, history_max_points_error = parse_query_int(qs, "history_max_points", 0, min_value=0, max_value=100_000)
    history_enabled = (qs.get("history") or ["1"])[0] != "0" and not query_bool(qs, "history_disabled")
    if token_since_error or token_resolution_error or token_history_start_error or token_history_end_error or history_start_error or history_end_error or history_resolution_error or history_max_points_error:
        request_error = token_since_error or token_resolution_error or token_history_start_error or token_history_end_error or history_start_error or history_end_error or history_resolution_error or history_max_points_error
        request.write_json(request_error.payload(), status=HTTPStatus.BAD_REQUEST)
        return
    app_profile, encoded = request.server.app.stats_sample_encoded_payload(
        since=since or 0,
        client_id=client_id,
        token_consumer=token_consumer,
        token_since=token_since or 0,
        token_resolution_seconds=token_resolution or 0,
        token_history_start=token_history_start if token_history_start_supplied else None,
        token_history_end=token_history_end if token_history_end_supplied else None,
        history_start=history_start or 0,
        history_end=history_end or 0,
        history_resolution_seconds=history_resolution or 0,
        history_max_points=history_max_points or 0,
        include_history=history_enabled,
    )
    build_ms = (time.perf_counter() - started) * 1000
    setattr(request, "_http_response_compute_ms", build_ms)
    details = {"stats_build_ms": round(build_ms, 3)}
    if isinstance(app_profile, dict):
        details.update(app_profile)
    setattr(request, "_http_response_performance_details", details)
    request.write_json_bytes(encoded)


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


def post_stats_history(request: Any, parsed: Any, route: Route) -> None:
    del parsed
    payload = _json_body(request, route)
    if payload is None:
        return
    response, status = request.server.app.record_stats_history_payload(payload)
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
    request.stream_client_events(
        channels=str(query_one(qs, "channels", "") or ""),
        client_id=str(query_one(qs, "client_id", "") or ""),
    )


def get_home(request: Any, parsed: Any, route: Route) -> None:
    del parsed, route
    sessions = request.share_sessions() if request.share_sessions() else request.server.app.sessions
    share_record = request.share_record()
    recent_sessions = request.server.app.tmux_recency_ordered_sessions(sessions)
    started = time.perf_counter()
    body = html_page(
        sessions,
        request.auth_identity().role,
        dev=getattr(request.server, "dev", False),
        dangerously_yolo=request.server.app.dangerously_yolo,
        share=request.share_bootstrap_payload(share_record) if share_record else None,
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
        "share": bool(share_record),
    })
    request.write_html(body)


def get_preview_popout(request: Any, parsed: Any, route: Route) -> None:
    del route
    request.handle_preview_popout_placeholder(parsed)


def get_pane_popout(request: Any, parsed: Any, route: Route) -> None:
    del route
    request.handle_pane_popout_placeholder(parsed)


def get_session_metadata(request: Any, parsed: Any, route: Route) -> None:
    del route
    qs = request_query(request, parsed)
    payload_fn = getattr(request.server.app, "session_metadata_payload", None) or request.server.app.transcripts_payload
    scoped_fn = getattr(request, "share_scoped_session_metadata_payload", None) or request.share_scoped_transcripts_payload
    request.write_json(scoped_fn(payload_fn(force=query_bool(qs, "force"))))


def get_transcripts(request: Any, parsed: Any, route: Route) -> None:
    get_session_metadata(request, parsed, route)


def get_tmux_session_exists(request: Any, parsed: Any, route: Route) -> None:
    del route
    qs = request_query(request, parsed)
    session = str(query_one(qs, "session", "") or "")
    request.write_app_result(request.server.app.tmux_session_exists_payload(session))


def get_agent_auth(request: Any, parsed: Any, route: Route) -> None:
    del route
    qs = request_query(request, parsed)
    request.write_json(request.server.app.agent_auth_payload(force=query_bool(qs, "force")))


def get_activity_summary(request: Any, parsed: Any, route: Route) -> None:
    del route
    qs = request_query(request, parsed)
    request.write_json(request.server.app.activity_summary_payload(
        force=query_bool(qs, "force"),
        locale=str(query_one(qs, "locale", "en") or "en"),
        session_scope=query_one(qs, "scope", "configured"),
        hours=query_one(qs, "hours", "24"),
    ))


def get_background_status(request: Any, parsed: Any, route: Route) -> None:
    del parsed, route
    request.write_app_result(request.server.app.background_owner_status_payload())


def get_performance_diagnostics(request: Any, parsed: Any, route: Route) -> None:
    del parsed, route
    request.write_json(request.server.app.performance_diagnostics_payload())


def get_system_status(request: Any, parsed: Any, route: Route) -> None:
    del parsed, route
    request.write_json(request.server.app.system_status_payload())


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
        lambda qs, lines: request.server.app.tmux_snapshot(str(query_one(qs, "session", "") or ""), lines),
    )


def get_tmux_signals(request: Any, parsed: Any, route: Route) -> None:
    del route
    qs = request_query(request, parsed)
    request.write_app_result(request.server.app.tmux_signals_payload(force=query_bool(qs, "force"), session=str(query_one(qs, "session", "") or "")))

def get_tmux_status(request: Any, parsed: Any, route: Route) -> None:
    del route
    qs = request_query(request, parsed)
    request.write_app_result(request.server.app.tmux_status_mode(str(query_one(qs, "session", "") or "")))


def get_transcript(request: Any, parsed: Any, route: Route) -> None:
    del route
    request.write_int_query_app_result(
        parsed,
        "lines",
        120,
        MAX_TRANSCRIPT_TAIL_LINES,
        lambda qs, lines: request.server.app.transcript_tail(str(query_one(qs, "session", "") or ""), lines),
    )


def get_context(request: Any, parsed: Any, route: Route) -> None:
    del route
    request.write_int_query_app_result(
        parsed,
        "messages",
        40,
        MAX_COMPACT_TRANSCRIPT_ITEMS,
        lambda qs, messages: request.server.app.context_tail(str(query_one(qs, "session", "") or ""), messages),
    )


def get_context_items(request: Any, parsed: Any, route: Route) -> None:
    del route
    request.write_int_query_app_result(
        parsed,
        "messages",
        40,
        MAX_COMPACT_TRANSCRIPT_ITEMS,
        lambda qs, messages: request.server.app.context_items(str(query_one(qs, "session", "") or ""), messages),
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
    session = query_one(qs, "session", None)
    request.write_app_result(request.server.app.auto_approve_status(session))


def get_notify(request: Any, parsed: Any, route: Route) -> None:
    del parsed, route
    request.write_json(request.server.app.notify_status())


def get_settings(request: Any, parsed: Any, route: Route) -> None:
    del parsed, route
    request.write_json(request.server.app.settings_payload())


def get_share_status(request: Any, parsed: Any, route: Route) -> None:
    del parsed, route
    if request.share_token():
        request.write_app_result(request.server.app.share_status_payload(request.share_token(), base_url=request.request_base_url()))
    else:
        request.write_app_result(request.server.app.active_share_payload(base_url=request.request_base_url()))


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
        lambda qs, limit: request.server.app.events_payload(query_one(qs, "session", None), limit),
    )


def get_search(request: Any, parsed: Any, route: Route) -> None:
    del route
    request.write_int_query_app_result(
        parsed,
        "limit",
        100,
        MAX_EVENT_TAIL_LINES,
        lambda qs, limit: request.server.app.search_payload(str(query_one(qs, "q", "") or ""), query_one(qs, "session", None), limit),
    )


def get_run_history(request: Any, parsed: Any, route: Route) -> None:
    del route
    qs = request_query(request, parsed)
    session = query_one(qs, "session", None)
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
        lambda hours: request.share_scoped_activity_result(request.server.app.activity_payload(hours=hours, visible=visible)),
    )


def get_session_files_batch(request: Any, parsed: Any, route: Route) -> None:
    del route
    qs = request_query(request, parsed)
    requested_sessions = query_list(qs, "session") or query_list(qs, "sessions")
    from_ref = query_one(qs, "from", None)
    to_ref = query_one(qs, "to", None)
    force = query_bool(qs, "force")
    share_sessions = request.share_sessions()
    repo_refs = parse_repo_refs_param(query_one(qs, "refs", None))

    def make_result(hours: float) -> tuple[Any, HTTPStatus]:
        scoped_sessions = requested_sessions
        if share_sessions:
            if not scoped_sessions:
                scoped_sessions = share_sessions
            blocked = [session for session in scoped_sessions if session not in share_sessions]
            if blocked:
                return error_payload(
                    "share token is scoped to a different session",
                    message_key="share.error.sessionScope",
                    status=HTTPStatus.FORBIDDEN,
                ), HTTPStatus.FORBIDDEN
        return request.server.app.session_files_batch_payload(scoped_sessions or None, hours, from_ref=from_ref, to_ref=to_ref, repo_refs=repo_refs, force=force)

    request.write_validated_float_result(qs, "hours", 24.0, ACTIVITY_MAX_HOURS, make_result)


def get_session_files(request: Any, parsed: Any, route: Route) -> None:
    del route
    qs = request_query(request, parsed)
    session = query_one(qs, "session", None)
    from_ref = query_one(qs, "from", None)
    to_ref = query_one(qs, "to", None)
    force = query_bool(qs, "force")
    share_sessions = request.share_sessions()
    repo_refs = parse_repo_refs_param(query_one(qs, "refs", None))

    def make_result(hours: float) -> tuple[Any, HTTPStatus]:
        if share_sessions and session not in share_sessions:
            return error_payload(
                "share token is scoped to a different session",
                message_key="share.error.sessionScope",
                status=HTTPStatus.FORBIDDEN,
            ), HTTPStatus.FORBIDDEN
        return request.server.app.session_files_payload(session, hours, from_ref=from_ref, to_ref=to_ref, repo_refs=repo_refs, force=force)

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
    session = str(query_one(qs, "session", "") or "")
    request.write_app_result(request.server.app.summary(session))


def _chat_write_result(request: Any, operation: Callable[[], dict[str, Any]], *, created: bool = False) -> None:
    if request.share_token_text():
        request.reject_forbidden(request.auth_identity(), "authenticated user")
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
    client_ip = str(request.client_address[0]) if isinstance(request.client_address, tuple) and request.client_address else ""

    def bootstrap_payload() -> dict[str, Any]:
        payload = request.server.app.chat_bootstrap(
            identity.username,
            str(query_one(qs, "browser_instance_id", "") or ""),
        )
        payload["client_ip"] = client_ip
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
    payload = _json_body(request, route)
    if payload is None:
        return
    identity = request.auth_identity()
    _chat_write_result(
        request,
        lambda: request.server.app.chat_send(
            identity.username,
            payload,
            request.request_locale_pref(),
            sender_ip=str(request.client_address[0]),
        ),
        created=True,
    )


def post_chat_yoagent(request: Any, parsed: Any, route: Route) -> None:
    del parsed
    payload = _json_body(request, route)
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
    payload = _json_body(request, route)
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
    payload = _json_body(request, route)
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


def get_fs_watch_diff(request: Any, parsed: Any, route: Route) -> None:
    del route
    qs = request_query(request, parsed)
    request.write_json(request.server.app.filesystem_watch_diff_payload(
        since_token=str(query_one(qs, "since", "") or ""),
        force_full=query_bool(qs, "full"),
    ))


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


def get_share_host_websocket(request: Any, parsed: Any, route: Route) -> None:
    del route
    request.websocket_share_host(parsed)


def get_share_ui_websocket(request: Any, parsed: Any, route: Route) -> None:
    del route
    request.websocket_share_ui(parsed)


def get_share_view_websocket(request: Any, parsed: Any, route: Route) -> None:
    del route
    request.websocket_share_view(parsed)


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
    session = str(query_one(qs, "session", "") or "")
    request.write_app_result(request.server.app.ensure_session(session))


def post_create_session(request: Any, parsed: Any, route: Route) -> None:
    del route
    qs = request_query(request, parsed)
    agent = str(query_one(qs, "agent", "claude") or "claude")
    dangerously_yolo = query_bool(qs, "dangerously_yolo", request.server.app.dangerously_yolo)
    terminal = str(query_one(qs, "terminal", "") or "")
    request.write_app_result(request.server.app.create_next_session(agent, dangerously_yolo, terminal))


def post_rename_session(request: Any, parsed: Any, route: Route) -> None:
    del route
    qs = request_query(request, parsed)
    session = str(query_one(qs, "session", "") or "")
    new_name = str(query_one(qs, "new_name", "") or "")
    request.write_app_result(request.server.app.rename_session(session, new_name))


def post_kill_session(request: Any, parsed: Any, route: Route) -> None:
    del route
    qs = request_query(request, parsed)
    session = str(query_one(qs, "session", "") or "")
    request.write_app_result(request.server.app.kill_session(session))


def post_upload(request: Any, parsed: Any, route: Route) -> None:
    del route
    qs = request_query(request, parsed)
    session = str(query_one(qs, "session", "") or "")
    editor_path = str(query_one(qs, "editor_path", "") or "")
    base_dir = str(query_one(qs, "base_dir", "") or "")
    request.write_app_result(request.handle_upload(session, editor_path=editor_path, base_dir=base_dir))


def post_auto_approve(request: Any, parsed: Any, route: Route) -> None:
    del route
    qs = request_query(request, parsed)
    session = str(query_one(qs, "session", "") or "")
    enabled = query_bool(qs, "enabled")
    request.write_app_result(request.server.app.set_auto_approve(session, enabled))


def post_attention_ack(request: Any, parsed: Any, route: Route) -> None:
    del parsed
    payload = _json_body(request, route)
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
    payload = _json_body(request, route)
    if payload is None:
        return
    request.write_json(request.server.app.save_settings(payload.get("settings", payload)))


def post_share_create(request: Any, parsed: Any, route: Route) -> None:
    del parsed
    payload = _json_body(request, route)
    if payload is None:
        return
    request.write_app_result(request.handle_share_create(payload))


def post_share_stop(request: Any, parsed: Any, route: Route) -> None:
    qs = request_query(request, parsed)
    token_or_short_id = str(query_one(qs, "token", "") or query_one(qs, "id", "") or "")
    if not token_or_short_id:
        payload = _json_body(request, route, allow_empty=True, allow_missing=True)
        if payload is None:
            return
        token_or_short_id = str(payload.get("token") or payload.get("short_id") or payload.get("id") or "")
    result = request.server.app.stop_active_share(token_or_short_id)
    request.server.close_inactive_share_upstreams()
    request.write_app_result(result)


def post_share_extend(request: Any, parsed: Any, route: Route) -> None:
    del parsed
    payload = _json_body(request, route)
    if payload is None:
        return
    token_or_short_id = str(payload.get("token") or payload.get("short_id") or payload.get("id") or "")
    add_seconds = payload.get("add_seconds", 600)
    result = request.server.app.extend_share_token(token_or_short_id, add_seconds, base_url=request.request_base_url())
    if result[1] == HTTPStatus.OK:
        token = str(result[0].get("token") or token_or_short_id)
        request.server.broadcast_share_status(token)
    request.write_app_result(result)


def post_share_debug_profile(request: Any, parsed: Any, route: Route) -> None:
    del parsed
    payload = _json_body(request, route)
    if payload is None:
        return
    token = request.share_token()
    if not token:
        request.write_json(
            error_payload(
                "share token required",
                message_key="share.error.tokenRequired",
                status=HTTPStatus.UNAUTHORIZED,
            ),
            status=HTTPStatus.UNAUTHORIZED,
        )
        return
    client_ip = request.client_address[0] if isinstance(request.client_address, tuple) and request.client_address else ""
    request.write_app_result(request.server.app.record_share_debug_profile(
        token,
        payload,
        ip=client_ip,
        user_agent=request.headers.get("User-Agent", ""),
    ))


def post_watch_roots(request: Any, parsed: Any, route: Route) -> None:
    del parsed
    payload = _json_body(request, route)
    if payload is None:
        return
    request.write_json(request.server.app.update_client_watch_roots(payload))


def post_drop_action(request: Any, parsed: Any, route: Route) -> None:
    del parsed
    payload = _json_body(request, route)
    if payload is None:
        return
    request.write_app_result(request.server.app.run_file_drop_action(payload))


def post_yoagent_chat(request: Any, parsed: Any, route: Route) -> None:
    del parsed
    payload = _json_body(request, route)
    if payload is None:
        return
    response, status = request.server.app.yoagent_controller.yoagent_chat(payload, access_role=request.auth_identity().role)
    request.write_json(response, status=status)


def post_yoagent_chat_cancel(request: Any, parsed: Any, route: Route) -> None:
    payload = _json_body(request, route)
    if payload is None:
        return
    request_id = unquote(parsed.path[len("/api/yoagent/chat/"):-len("/cancel")]).strip("/")
    response, status = request.server.app.yoagent_controller.cancel_yoagent_chat(str(payload.get("request_id") or request_id))
    request.write_json(response, status=status)


def post_yoagent_preview_send(request: Any, parsed: Any, route: Route) -> None:
    del parsed
    payload = _json_body(request, route)
    if payload is None:
        return
    response, status = request.server.app.yoagent_controller.preview_yoagent_send_action(payload)
    request.write_json(response, status=status)


def post_yoagent_execute_send(request: Any, parsed: Any, route: Route) -> None:
    del parsed
    payload = _json_body(request, route)
    if payload is None:
        return
    response, status = request.server.app.yoagent_controller.execute_yoagent_send_action(payload)
    request.write_json(response, status=status)


def post_yoagent_intent(request: Any, parsed: Any, route: Route) -> None:
    del parsed
    payload = _json_body(request, route)
    if payload is None:
        return
    response, status = request.server.app.yoagent_controller.yoagent_intent(payload)
    request.write_json(response, status=status)


def post_yoagent_jobs(request: Any, parsed: Any, route: Route) -> None:
    del parsed
    payload = _json_body(request, route)
    if payload is None:
        return
    response, status = request.server.app.yoagent_controller.create_yoagent_job(payload)
    request.write_json(response, status=status)


def post_yoagent_job_confirm(request: Any, parsed: Any, route: Route) -> None:
    payload = _json_body(request, route)
    if payload is None:
        return
    job_id = unquote(parsed.path[len("/api/yoagent/jobs/"):-len("/confirm")]).strip("/")
    response, status = request.server.app.yoagent_controller.confirm_yoagent_job(str(payload.get("id") or job_id))
    request.write_json(response, status=status)


def post_yoagent_job_cancel(request: Any, parsed: Any, route: Route) -> None:
    payload = _json_body(request, route)
    if payload is None:
        return
    job_id = unquote(parsed.path[len("/api/yoagent/jobs/"):-len("/cancel")]).strip("/")
    response, status = request.server.app.yoagent_controller.cancel_yoagent_job(str(payload.get("id") or job_id))
    request.write_json(response, status=status)


def post_yoagent_jobs_cancel_session(request: Any, parsed: Any, route: Route) -> None:
    del parsed
    payload = _json_body(request, route)
    if payload is None:
        return
    response, status = request.server.app.yoagent_controller.cancel_yoagent_jobs_for_session(str(payload.get("session") or ""))
    request.write_json(response, status=status)


def post_yoagent_wait_clear(request: Any, parsed: Any, route: Route) -> None:
    payload = _json_body(request, route)
    if payload is None:
        return
    wait_id = unquote(parsed.path[len("/api/yoagent/waits/"):-len("/clear")]).strip("/")
    response, status = request.server.app.yoagent_controller.clear_yoagent_action_wait(str(payload.get("id") or wait_id))
    request.write_json(response, status=status)


def post_yoagent_skill_file_upsert(request: Any, parsed: Any, route: Route) -> None:
    del parsed
    payload = _json_body(request, route)
    if payload is None:
        return
    response, status = request.server.app.upsert_yoagent_skill_file(payload)
    request.write_json(response, status=status)


def post_yoagent_skill_file_delete(request: Any, parsed: Any, route: Route) -> None:
    del parsed
    payload = _json_body(request, route)
    if payload is None:
        return
    response, status = request.server.app.delete_yoagent_skill_file(payload)
    request.write_json(response, status=status)


def post_yoagent_prewarm(request: Any, parsed: Any, route: Route) -> None:
    del parsed
    payload = _json_body(request, route)
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
    session = qs.get("session", [""])[0]
    request.write_app_result(request.server.app.tmux_next_window(session))

def post_tmux_status(request: Any, parsed: Any, route: Route) -> None:
    del route
    qs = request_query(request, parsed)
    request.write_app_result(request.server.app.cycle_tmux_status_mode(str(query_one(qs, "session", "") or "")))


def post_tmux_window(request: Any, parsed: Any, route: Route) -> None:
    del route
    qs = request_query(request, parsed)
    session = qs.get("session", [""])[0]
    window = qs.get("window", [""])[0]
    payload, status = request.server.app.tmux_select_window(session, window)
    request.write_json(payload, status=status)


def post_tmux_copy_selection(request: Any, parsed: Any, route: Route) -> None:
    del route
    qs = request_query(request, parsed)
    session = str(query_one(qs, "session", "") or "")
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
    Route("GET", "/static/*", PUBLIC, get_static_asset, group="core", share_access=SHARE_ACCESS_READONLY),
    Route("GET", "/api/auth-setup", PUBLIC, get_auth_setup, group="core"),
    Route("GET", "/login", PUBLIC, get_login, group="core"),
    Route("GET", "/logout", PUBLIC, get_logout, group="core"),
    Route("GET", "/api/ping", "readonly", get_ping, group="core", share_access=SHARE_ACCESS_READONLY),
    Route("GET", "/api/stats-sample", "readonly", get_stats_sample, group="core"),
    Route("GET", "/api/pricing-catalog", "readonly", get_pricing_catalog, group="core"),
    Route("GET", "/api/update-status", "admin", get_update_status, group="core"),
    Route("GET", "/api/dev-reload", "readonly", get_dev_reload, group="core"),
    Route("GET", "/api/client-events", "readonly", get_client_events, group="core"),
    Route("GET", "/", "readonly", get_home, group="core", share_access=SHARE_ACCESS_READONLY),
    Route("GET", "/preview-popout", "readonly", get_preview_popout, group="core"),
    Route("GET", "/pane-popout", "readonly", get_pane_popout, group="core"),
    Route("GET", "/api/session-metadata", "readonly", get_session_metadata, group="core", share_access=SHARE_ACCESS_READONLY),
    Route("GET", "/api/transcripts", "readonly", get_transcripts, group="core", share_access=SHARE_ACCESS_READONLY),
    Route("GET", "/api/agent-auth", "readonly", get_agent_auth, group="core"),
    Route("GET", "/api/activity-summary", "readonly", get_activity_summary, group="core"),
    Route("GET", "/api/background/status", "readonly", get_background_status, group="core"),
    Route("GET", "/api/system-status", "readonly", get_system_status, group="core"),
    Route("GET", "/api/diagnostics/performance", "admin", get_performance_diagnostics, group="core"),
    Route("GET", "/api/auto-approve", "readonly", get_auto_approve, group="core"),
    Route("GET", "/api/notify", "readonly", get_notify, group="core"),
    Route("GET", "/api/settings", "readonly", get_settings, group="core"),
    Route("GET", "/api/watched-prs", "readonly", get_watched_prs, group="core"),
    Route("GET", "/api/yolo-rules", "readonly", get_yolo_rules, group="core"),
    Route("GET", "/api/events", "readonly", get_events, group="core"),
    Route("GET", "/api/search", "readonly", get_search, group="core"),
    Route("GET", "/api/run-history", "readonly", get_run_history, group="core"),
    Route("GET", "/api/activity", "readonly", get_activity, group="core", share_access=SHARE_ACCESS_READONLY),
    Route("GET", "/api/session-files-batch", "readonly", get_session_files_batch, group="core", share_access=SHARE_ACCESS_READONLY),
    Route("GET", "/api/session-files", "readonly", get_session_files, group="core", share_access=SHARE_ACCESS_READONLY),
    Route("GET", "/api/summary", "readonly", get_summary, group="core"),
    Route("GET", "/api/tmux-session-exists", "readonly", get_tmux_session_exists, group="core"),
    Route("POST", "/login", PUBLIC, post_login, group="core"),
    Route("POST", "/api/self-update", "admin", post_self_update, group="core"),
    Route("POST", "/api/stats-history", "readonly", post_stats_history, body_limit=128 * 1024, group="core"),
    Route("POST", "/api/pricing-catalog/refresh", "admin", post_pricing_catalog_refresh, group="core"),
    Route("POST", "/api/background/claim", "admin", post_background_claim, group="core"),
    Route("POST", "/api/ensure-session", "admin", post_ensure_session, group="core"),
    Route("POST", "/api/create-session", "admin", post_create_session, group="core"),
    Route("POST", "/api/rename-session", "admin", post_rename_session, group="core"),
    Route("POST", "/api/kill-session", "admin", post_kill_session, group="core"),
    Route("POST", "/api/upload", "admin", post_upload, group="core"),
    Route("POST", "/api/auto-approve", "admin", post_auto_approve, group="core"),
    Route("POST", "/api/attention-ack", "admin", post_attention_ack, body_limit=16 * 1024, group="core"),
    Route("POST", "/api/notify", "admin", post_notify, group="core"),
    Route("POST", "/api/settings", "admin", post_settings, body_limit=64 * 1024, group="core"),
    Route("POST", "/api/watch/roots", "admin", post_watch_roots, body_limit=64 * 1024, group="core"),
    Route("POST", "/api/drop-action/run", "admin", post_drop_action, body_limit=64 * 1024, group="core"),
    Route("POST", "/api/yolo-rules/reload", "admin", post_yolo_rules_reload, group="core"),
    Route("POST", "/api/yolo-rules/open", "admin", post_yolo_rules_open, group="core"),
    Route("POST", "/api/event", "readonly", post_event, group="core"),
)

SHARE_ROUTES = (
    Route("GET", "/share/*", PUBLIC, get_share_shell, group="share", share_access=SHARE_ACCESS_READONLY),
    Route("GET", "/api/share", share_token_readonly_role, get_share_status, group="share", share_access=SHARE_ACCESS_READONLY),
    Route("GET", "/ws/share-host", "admin", get_share_host_websocket, group="share"),
    Route("GET", "/ws/share-ui", "readonly", get_share_ui_websocket, group="share", share_access=SHARE_ACCESS_READONLY),
    Route("GET", "/ws/share-view", "readonly", get_share_view_websocket, group="share", share_access=SHARE_ACCESS_READONLY),
    Route("POST", "/api/share", "admin", post_share_create, body_limit=16 * 1024, group="share"),
    Route("POST", "/api/share/stop", "admin", post_share_stop, body_limit=4096, group="share"),
    Route("POST", "/api/share/extend", "admin", post_share_extend, body_limit=4096, group="share"),
    Route("POST", "/api/share/debug-profile", share_token_readonly_role, post_share_debug_profile, body_limit=64 * 1024, group="share", share_access=SHARE_ACCESS_READONLY),
)

YOAGENT_ROUTES = (
    Route("GET", "/api/yoagent/skills", "admin", get_yoagent_skills, group="yoagent"),
    Route("GET", "/api/yoagent/skill-files", "admin", get_yoagent_skill_files, group="yoagent"),
    Route("GET", "/api/yoagent/conversation", "admin", get_yoagent_conversation, group="yoagent"),
    Route("GET", "/api/yoagent/jobs", "admin", get_yoagent_jobs, group="yoagent"),
    Route("POST", "/api/yoagent/chat", yoagent_chat_post_role, post_yoagent_chat, body_limit=64 * 1024, group="yoagent"),
    Route("POST", "/api/yoagent/chat/*/cancel", "admin", post_yoagent_chat_cancel, body_limit=16 * 1024, group="yoagent"),
    Route("POST", "/api/yoagent/actions/preview-send", "admin", post_yoagent_preview_send, body_limit=64 * 1024, group="yoagent"),
    Route("POST", "/api/yoagent/actions/execute-send", "admin", post_yoagent_execute_send, body_limit=16 * 1024, group="yoagent"),
    Route("POST", "/api/yoagent/intent", "admin", post_yoagent_intent, body_limit=64 * 1024, group="yoagent"),
    Route("POST", "/api/yoagent/jobs", "admin", post_yoagent_jobs, body_limit=64 * 1024, group="yoagent"),
    Route("POST", "/api/yoagent/jobs/cancel-session", "admin", post_yoagent_jobs_cancel_session, body_limit=16 * 1024, group="yoagent"),
    Route("POST", "/api/yoagent/jobs/*/confirm", "admin", post_yoagent_job_confirm, body_limit=16 * 1024, group="yoagent"),
    Route("POST", "/api/yoagent/jobs/*/cancel", "admin", post_yoagent_job_cancel, body_limit=16 * 1024, group="yoagent"),
    Route("POST", "/api/yoagent/waits/*/clear", "admin", post_yoagent_wait_clear, body_limit=16 * 1024, group="yoagent"),
    Route("POST", "/api/yoagent/skill-files/upsert", "admin", post_yoagent_skill_file_upsert, body_limit=64 * 1024, group="yoagent"),
    Route("POST", "/api/yoagent/skill-files/delete", "admin", post_yoagent_skill_file_delete, body_limit=16 * 1024, group="yoagent"),
    Route("POST", "/api/yoagent/prewarm", "admin", post_yoagent_prewarm, body_limit=64 * 1024, group="yoagent"),
    Route("POST", "/api/yoagent/reset", "admin", post_yoagent_reset, group="yoagent"),
)

CHAT_ROUTES = (
    Route("GET", "/api/chat/bootstrap", "readonly", get_chat_bootstrap, group="chat"),
    Route("GET", "/api/chat/page", "readonly", get_chat_page, group="chat"),
    Route("GET", "/api/chat/delta", "readonly", get_chat_delta, group="chat"),
    Route("GET", "/api/chat/context", "readonly", get_chat_context, group="chat"),
    Route("GET", "/api/chat/search", "readonly", get_chat_search, group="chat"),
    Route("POST", "/api/chat/send", "readonly", post_chat_send, body_limit=12 * 1024, group="chat"),
    Route("POST", "/api/chat/yoagent", "readonly", post_chat_yoagent, body_limit=4096, group="chat"),
    Route("POST", "/api/chat/typing", "readonly", post_chat_typing, body_limit=4096, group="chat"),
    Route("POST", "/api/chat/read", "readonly", post_chat_read, body_limit=4096, group="chat"),
)

FILESYSTEM_ROUTES = (
    Route("GET", "/api/fs/list", "readonly", get_fs_list, group="filesystem", share_access=SHARE_ACCESS_READONLY),
    Route("GET", "/api/fs/search", "readonly", get_fs_search, group="filesystem"),
    Route("GET", "/api/fs/index-status", "readonly", get_fs_index_status, group="filesystem", share_access=SHARE_ACCESS_READONLY),
    Route("GET", "/api/fs/read", "readonly", get_fs_read, group="filesystem", share_access=SHARE_ACCESS_SCOPED_FILE),
    Route("GET", "/api/fs/info", "readonly", get_fs_info, group="filesystem", share_access=SHARE_ACCESS_READONLY),
    Route("GET", "/api/fs/diff", "readonly", get_fs_diff, group="filesystem", share_access=SHARE_ACCESS_SCOPED_FILE),
    Route("GET", "/api/fs/watch-diff", "readonly", get_fs_watch_diff, group="filesystem", share_access=SHARE_ACCESS_READONLY),
    Route("GET", "/api/blame", "readonly", get_blame, group="filesystem"),
    Route("GET", "/api/fs/raw", "readonly", get_fs_raw, group="filesystem", share_access=SHARE_ACCESS_SCOPED_FILE),
    Route("GET", "/api/fs/zip", "readonly", get_fs_zip, group="filesystem"),
    Route("GET", "/api/fs/count", "readonly", get_fs_count, group="filesystem"),
    Route("GET", "/api/fs/html-preview", "readonly", get_fs_html_preview, group="filesystem"),
    Route("POST", "/api/fs/batch", share_token_readonly_role, post_fs_batch, body_limit=64 * 1024, group="filesystem", share_access=SHARE_ACCESS_READONLY),
    Route("POST", "/api/fs/write", "admin", post_fs_write, group="filesystem"),
    Route("POST", "/api/fs/delete", "admin", post_fs_delete, body_limit=4096, group="filesystem"),
    Route("POST", "/api/fs/unindex", "admin", post_fs_unindex, body_limit=4096, group="filesystem"),
    Route("POST", "/api/fs/rename", "admin", post_fs_rename, body_limit=4096, group="filesystem"),
    Route("POST", "/api/fs/mkdir", "admin", post_fs_mkdir, body_limit=4096, group="filesystem"),
)

TMUX_ROUTES = (
    Route("GET", "/api/tmux", "readonly", get_tmux, group="tmux"),
    Route("GET", "/api/tmux-signals", "readonly", get_tmux_signals, group="tmux"),
    Route("GET", "/api/tmux-status", "readonly", get_tmux_status, group="tmux"),
    Route("GET", "/api/transcript", "readonly", get_transcript, group="tmux"),
    Route("GET", "/api/context", "readonly", get_context, group="tmux"),
    Route("GET", "/api/context-items", "readonly", get_context_items, group="tmux"),
    Route("GET", "/api/context-stream", "readonly", get_context_stream, group="tmux"),
    Route("GET", "/api/summary-stream", "admin", get_summary_stream, group="tmux"),
    Route("GET", "/ws", "readonly", get_websocket, group="tmux", share_access=SHARE_ACCESS_READONLY),
    Route("POST", "/api/tmux-next", "admin", post_tmux_next, group="tmux"),
    Route("POST", "/api/tmux-status", "admin", post_tmux_status, group="tmux"),
    Route("POST", "/api/tmux-window", "admin", post_tmux_window, group="tmux"),
    Route("POST", "/api/tmux-copy-selection", "admin", post_tmux_copy_selection, group="tmux"),
)

ROUTE_GROUPS = {
    "core": CORE_ROUTES,
    "share": SHARE_ROUTES,
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
