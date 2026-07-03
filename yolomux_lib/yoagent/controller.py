# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Controller for Yoagent jobs, actions, sends, and chat flow."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import secrets
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from http import HTTPStatus
from pathlib import Path
from typing import Any

from ..agent_tui import classify_agent_pane
from ..agent_tui import clear_composer
from ..agent_tui import composer_text_is_idle_placeholder
from ..agent_tui import text_still_in_composer
from ..agent_tui import visible_composer_source
from ..agent_tui import visible_composer_text
from ..activity_summary import deterministic_yoagent_reply
from ..activity_summary import yoagent_context_lines
from ..activity_summary import yoagent_question_requests_session_list
from ..activity_summary import yoagent_question_requests_work_next
from ..common import SessionInfo
from ..common import truncate_text
from ..locales import message_fields
from ..locales import message_descriptor
from ..locales import normalize_locale
from ..locales import user_message_payload
from ..session_files import classify_change
from ..session_files import scan_claude_transcript
from ..session_files import scan_codex_transcript
from ..prompt_detector import selected_prompt_option
from ..tmux_utils import tmux_move_to_option
from ..tmux_utils import tmux_run
from ..tmux_utils import tmux_send_enter
from ..tmux_utils import tmux_session_target
from ..transcripts import codex_event_text
from ..transcripts import compact_transcript_items
from ..transcripts import transcript_delta_result_state
from . import conversation as yoagent_conversation
from .actions import parse_yoagent_action_intent
from .actions import parse_yoagent_job_intent
from .actions import parse_yoagent_skill_file_intent
from .actions import redacted_action_preview
from .actions import redacted_action_text
from .backends import YOAGENT_STARTUP_QUESTION
from .backends import strip_yoagent_hidden_thinking
from .backends import yoagent_cli_fallback_reason
from .backends import yoagent_response_detail_rows
from .backends import yoagent_response_ms
from .preferences import parse_settings_read
from .preferences import parse_settings_write
from .preferences import product_state_needs_activity
from .preferences import yoagent_operator_response
from .preferences import yoagent_text
from .preferences import yoagent_user_message_text
from .session_summaries import YoagentSessionSummariesMixin
from .transports import TMUX_LEGACY_TRANSPORT_ID
from .transports import normalize_yoagent_transport_id
from .backends import YoagentBackendsMixin


YOAGENT_ACTION_PREVIEW_TTL_SECONDS = 5 * 60
YOAGENT_ACTION_TEXT_LIMIT = 4000
YOAGENT_ACTION_RESULT_WAIT_SECONDS = 180.0
YOAGENT_ACTION_RESULT_POLL_SECONDS = 1.0
YOAGENT_ACTION_RESULT_MAX_CHARS = 6000
YOAGENT_ACTION_AGENT_KINDS = {"claude", "codex"}
YOAGENT_ACTION_ACCEPTING_SCREEN_KEYS = {"idle", "done", "input-draft"}
YOAGENT_JOB_NEEDS_INPUT_SCREEN_KEYS = {"needs-input"}
YOAGENT_JOB_BLOCKED_SCREEN_KEYS = {"approval", "needs-approval", "yolo-approval", "error", "disconnected"}
YOAGENT_JOBS_STATE_KEY = "yoagent_jobs"
YOAGENT_JOB_MAX_ITEMS = 200
YOAGENT_JOB_POLL_SECONDS = 1.0
YOAGENT_JOB_DEFAULT_TIMEOUT_MINUTES = 120
YOAGENT_JOB_IDLE_QUIET_SECONDS = 3.0
YOAGENT_JOB_TYPE_MESSAGE_KEYS = {
    "notify_all_idle": "yoagent.jobs.type.notifyAllIdle",
    "notify_session_blocked": "yoagent.jobs.type.notifySessionBlocked",
    "notify_session_done_after_working": "yoagent.jobs.type.notifySessionDoneAfterWorking",
    "notify_session_idle": "yoagent.jobs.type.notifySessionIdle",
    "notify_session_needs_input": "yoagent.jobs.type.notifySessionNeedsInput",
    "result_watch": "yoagent.jobs.type.resultWatch",
    "wait_then_send": "yoagent.jobs.type.waitThenSend",
}
YOAGENT_CHAT_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,120}$")
YOAGENT_ACTIVITY_CONTEXT_RE = re.compile(
    r"\b(?:activity|agent|ask\?|branch|changed?|codex|claude|diff|differ|done|edited|file|finder|modified|pane|path|pending|pr|pull request|repo|session|tab|tmux|transcript|window|work|working)\b",
    re.IGNORECASE,
)


def yoagent_job_type_descriptor(job_type: str) -> dict[str, Any]:
    key = YOAGENT_JOB_TYPE_MESSAGE_KEYS.get(str(job_type or ""), "yoagent.jobs.type.unknown")
    return message_descriptor(key, yoagent_text("en", key))


def yoagent_fallback_reason_fields(fallback_reason: str, cli: dict[str, Any] | None = None) -> dict[str, Any]:
    descriptor = cli.get("fallback_reason_message") if isinstance(cli, dict) else None
    if isinstance(descriptor, dict):
        return message_fields(
            "fallback_reason",
            str(descriptor.get("key") or ""),
            fallback_reason or descriptor.get("fallback") or "",
            descriptor.get("params") if isinstance(descriptor.get("params"), dict) else {},
        )
    return message_fields("fallback_reason", "", fallback_reason)


def yoagent_job_message_fields(
    field: str,
    source: dict[str, Any] | None = None,
    *,
    source_field: str = "message",
    default_key: str = "",
    default_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    value = source if isinstance(source, dict) else {}
    key = str(value.get(f"{source_field}_key") or default_key)
    raw_params = value.get(f"{source_field}_params")
    params = raw_params if isinstance(raw_params, dict) else dict(default_params or {})
    fallback = str(value.get(source_field) or "")
    if not fallback and key:
        fallback = yoagent_text("en", key, **params)
    return message_fields(field, key, fallback, params)


def yoagent_job_default_action(message_key: str, params: dict[str, Any], custom_message: object = "") -> dict[str, Any]:
    custom = str(custom_message or "").strip()
    if custom:
        return {"type": "notify_user", **message_fields("message", "", custom)}
    return {
        "type": "notify_user",
        **message_fields("message", message_key, yoagent_text("en", message_key, **params), params),
    }


YOAGENT_LIVE_EXTERNAL_RE = re.compile(
    r"\b(?:weather|forecast|temperature|stock|stocks|market|markets|price|prices|news|headline|headlines|traffic|flight|flights|sports|score|scores|exchange rate)\b",
    re.IGNORECASE,
)


def yoagent_question_needs_activity_context(question: str) -> bool:
    text = str(question or "").strip()
    if not text:
        return False
    return (
        yoagent_question_requests_work_next(text)
        or yoagent_question_requests_session_list(text)
        or product_state_needs_activity(text)
        or bool(YOAGENT_ACTIVITY_CONTEXT_RE.search(text))
    )


def yoagent_question_requests_external_tools(question: str) -> bool:
    text = str(question or "").strip()
    return bool(text and YOAGENT_LIVE_EXTERNAL_RE.search(text))


def yoagent_chat_tool_capabilities(backend: str, invocation: str, locale: str = "en") -> dict[str, Any]:
    normalized_backend = str(backend or "").strip().lower()
    normalized_invocation = str(invocation or "").strip().lower()
    tools: list[str] = []
    reason = ""
    if normalized_invocation != "cli":
        reason = yoagent_text(
            locale,
            "yoagent.tools.invocationUnavailable",
            backend=normalized_backend or yoagent_text(locale, "yoagent.tools.selectedBackend"),
            invocation=normalized_invocation or yoagent_text(locale, "common.unknown"),
        )
    elif normalized_backend == "codex":
        tools = ["web_search"]
    elif normalized_backend == "claude":
        tools = ["default"]
    else:
        reason = yoagent_text(locale, "yoagent.tools.noBackend")
    return {
        "backend": normalized_backend,
        "invocation": normalized_invocation,
        "enabled": bool(tools),
        "tools": tools,
        "reason": reason,
    }


def yoagent_live_external_data_reply(capabilities: dict[str, Any], locale: str = "en") -> str:
    reason = str(capabilities.get("reason") or yoagent_text(locale, "yoagent.tools.unavailableReason")).strip()
    return yoagent_text(locale, "yoagent.tools.unavailable", reason=reason)


@dataclass
class YoagentChatContext:
    controller: Any
    payload: dict[str, Any]
    access_role: str
    started: float
    question: str
    request_id: str
    stream_id: str
    history: list[dict[str, Any]]
    settings: dict[str, Any]
    locale: str
    force_activity_context: bool
    activity_payload_cache: dict[str, Any] | None = None
    context_lines_cache: list[str] | None = None

    def get_activity_payload(self) -> dict[str, Any]:
        if self.activity_payload_cache is None:
            self.activity_payload_cache = self.controller.deps.yoagent_activity_payload(force=self.force_activity_context)
        return self.activity_payload_cache

    def get_context_lines(self) -> list[str]:
        if self.context_lines_cache is None:
            self.context_lines_cache = yoagent_context_lines(self.get_activity_payload())
        return self.context_lines_cache

    def base_response(
        self,
        answer: str,
        *,
        actions: list[dict[str, Any]] | None = None,
        backend: str = "yolomux",
        backend_used: str = "yolomux",
        fallback_reason: str = "",
        details: str = "",
        detail_rows: list[dict[str, Any]] | None = None,
        cli: dict[str, Any] | None = None,
        include_activity: bool = False,
    ) -> dict[str, Any]:
        response_activity = self.get_activity_payload() if include_activity else {}
        cli_payload = cli or {}
        return {
            "answer": answer,
            "actions": actions or [],
            "backend": backend,
            "backend_used": backend_used,
            "fallback": bool(fallback_reason),
            **yoagent_fallback_reason_fields(fallback_reason, cli_payload),
            "details": details,
            "detail_rows": detail_rows or [],
            "cli": cli_payload,
            "context_lines": self.get_context_lines() if include_activity else [],
            "generated_at": response_activity.get("generated_at"),
            "session_order": response_activity.get("session_order", []),
        }

    def finish(self, response: dict[str, Any]) -> tuple[dict[str, Any], HTTPStatus]:
        controller = self.controller
        if controller.deps.yoagent_chat_request_cancelled(self.request_id):
            return self.finish_cancelled()
        response.setdefault("answered_at", datetime.now(timezone.utc).isoformat())
        response.setdefault("request_id", self.request_id)
        response.setdefault("stream_id", self.stream_id)
        timing = response.get("timing") if isinstance(response.get("timing"), dict) else {}
        timing.setdefault("ttfr_ms", round((time.monotonic() - self.started) * 1000, 3))
        response["timing"] = timing
        answer_text = str(response.get("answer") or "")
        answer_text, hidden_thinking_removed = strip_yoagent_hidden_thinking(answer_text)
        response["answer"] = answer_text
        if hidden_thinking_removed:
            response["hidden_thinking_removed"] = True
        detail_rows = response.get("detail_rows") if isinstance(response.get("detail_rows"), list) else []
        response["detail_rows"] = [*detail_rows, *yoagent_response_detail_rows(response)]
        actions = response.get("actions") if isinstance(response.get("actions"), list) else []
        if answer_text:
            stream_fields = controller.deps.yoagent_stream_auxiliary_message_fields(str(response.get("stream_id") or ""))
            controller.record_yoagent_message(
                "assistant",
                answer_text,
                actions=actions,
                details=str(response.get("details") or ""),
                detail_rows=response["detail_rows"],
                response_ms=yoagent_response_ms(response),
                **stream_fields,
            )
        response["conversation"] = controller.yoagent_conversation_payload()
        controller.deps.complete_yoagent_chat_request(self.request_id)
        return response, HTTPStatus.OK

    def finish_cancelled(self) -> tuple[dict[str, Any], HTTPStatus]:
        controller = self.controller
        controller.publish_yoagent_stream_delta(self.stream_id, "", phase="stopped", done=True, aborted=True, auxiliary_done=True)
        controller.deps.complete_yoagent_chat_request(self.request_id)
        return {
            "ok": True,
            "cancelled": True,
            "request_id": self.request_id,
            "stream_id": self.stream_id,
            "conversation": controller.yoagent_conversation_payload(),
        }, HTTPStatus.OK


class YoagentController(YoagentBackendsMixin, YoagentSessionSummariesMixin):
    def __init__(self, deps: Any):
        object.__setattr__(self, "deps", deps)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.deps, name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "deps" or name.startswith("_"):
            object.__setattr__(self, name, value)
            return
        setattr(self.deps, name, value)

    def normalize_yoagent_chat_request_id(self, value: Any) -> str:
        text = str(value or "").strip()
        return text if YOAGENT_CHAT_REQUEST_ID_RE.match(text) else ""

    def prune_yoagent_chat_requests_locked(self, now: float | None = None) -> None:
        cutoff = (now if now is not None else time.time()) - 300
        requests = self.yoagent_chat_requests
        for request_id, entry in list(requests.items()):
            if bool(entry.get("active")):
                continue
            if float(entry.get("updated_ts") or entry.get("created_ts") or 0.0) < cutoff:
                requests.pop(request_id, None)

    def register_yoagent_chat_request(self, request_id: str, stream_id: str, backend: str = "") -> threading.Event:
        safe_request_id = self.normalize_yoagent_chat_request_id(request_id) or f"chat-{uuid.uuid4().hex}"
        safe_stream_id = self.normalize_yoagent_chat_request_id(stream_id) or safe_request_id
        cancel_event = threading.Event()
        with self.yoagent_chat_request_lock:
            self.prune_yoagent_chat_requests_locked()
            self.yoagent_chat_requests[safe_request_id] = {
                "request_id": safe_request_id,
                "stream_id": safe_stream_id,
                "backend": str(backend or ""),
                "created_ts": time.time(),
                "updated_ts": time.time(),
                "active": True,
                "cancelled": False,
                "cancel_event": cancel_event,
                "interrupt": None,
            }
        return cancel_event

    def set_yoagent_chat_request_interrupt(self, request_id: str, interrupt: Any) -> None:
        safe_request_id = self.normalize_yoagent_chat_request_id(request_id)
        if not safe_request_id:
            return
        with self.yoagent_chat_request_lock:
            entry = self.yoagent_chat_requests.get(safe_request_id)
            if entry is not None:
                entry["interrupt"] = interrupt if callable(interrupt) else None
                entry["updated_ts"] = time.time()

    def yoagent_chat_request_cancel_event(self, request_id: str) -> threading.Event | None:
        safe_request_id = self.normalize_yoagent_chat_request_id(request_id)
        if not safe_request_id:
            return None
        with self.yoagent_chat_request_lock:
            entry = self.yoagent_chat_requests.get(safe_request_id)
            event = entry.get("cancel_event") if isinstance(entry, dict) else None
        return event if hasattr(event, "is_set") and hasattr(event, "set") else None

    def yoagent_chat_request_cancelled(self, request_id: str) -> bool:
        event = self.yoagent_chat_request_cancel_event(request_id)
        return bool(event is not None and event.is_set())

    def complete_yoagent_chat_request(self, request_id: str) -> None:
        safe_request_id = self.normalize_yoagent_chat_request_id(request_id)
        if not safe_request_id:
            return
        with self.yoagent_chat_request_lock:
            entry = self.yoagent_chat_requests.get(safe_request_id)
            if entry is not None:
                entry["active"] = False
                entry["interrupt"] = None
                entry["updated_ts"] = time.time()

    def interrupt_yoagent_claude_process(self, process: subprocess.Popen[str]) -> dict[str, Any]:
        if process.poll() is not None:
            return {"ok": True, "interrupted": False, "reason": "claude process already exited"}
        process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=1)
        return {"ok": True, "interrupted": True, "transport": "claude-stream-json"}

    def cancel_yoagent_chat(self, request_id: str) -> tuple[dict[str, Any], HTTPStatus]:
        safe_request_id = self.normalize_yoagent_chat_request_id(request_id)
        if not safe_request_id:
            return {"ok": False, **user_message_payload("common.requestFailed", "missing YO!agent request id")}, HTTPStatus.BAD_REQUEST
        interrupt = None
        stream_id = safe_request_id
        with self.yoagent_chat_request_lock:
            entry = self.yoagent_chat_requests.get(safe_request_id)
            if not isinstance(entry, dict):
                return {"ok": True, "cancelled": False, "request_id": safe_request_id, "reason": "request is not active"}, HTTPStatus.OK
            entry["cancelled"] = True
            entry["updated_ts"] = time.time()
            event = entry.get("cancel_event")
            if hasattr(event, "set"):
                event.set()
            stream_id = str(entry.get("stream_id") or safe_request_id)
            interrupt = entry.get("interrupt")
        interrupt_result: dict[str, Any] = {}
        if callable(interrupt):
            try:
                value = interrupt()
                interrupt_result = value if isinstance(value, dict) else {"ok": True, "result": str(value)}
            except Exception as exc:
                interrupt_result = {"ok": False, "error": str(exc)}
        self.publish_yoagent_stream_delta(stream_id, "", phase="stopped", done=True, aborted=True, auxiliary_done=True)
        return {"ok": True, "cancelled": True, "request_id": safe_request_id, "stream_id": stream_id, "interrupt": interrupt_result}, HTTPStatus.OK

    def yoagent_job_prompt_text(self, action: dict[str, Any]) -> str:
        if str(action.get("type") or "") != "send_prompt":
            return ""
        return truncate_text(str(action.get("text") or ""), YOAGENT_ACTION_TEXT_LIMIT)


    def yoagent_job_transport_id(self, target: dict[str, Any], action: dict[str, Any], result: dict[str, Any] | None = None) -> str:
        send_result = result.get("send") if isinstance(result, dict) and isinstance(result.get("send"), dict) else {}
        action_result = result.get("action") if isinstance(result, dict) and isinstance(result.get("action"), dict) else {}
        action_target = action_result.get("target") if isinstance(action_result.get("target"), dict) else {}
        candidates = [
            send_result.get("transport"),
            action.get("transport"),
            target.get("transport"),
            action_target.get("transport"),
        ]
        for candidate in candidates:
            raw = str(candidate or "").strip()
            if raw:
                return normalize_yoagent_transport_id(raw)
        return ""


    def yoagent_job_result_marker_from_result(self, result: dict[str, Any]) -> dict[str, Any]:
        send_result = result.get("send") if isinstance(result.get("send"), dict) else {}
        marker = result.get("result_marker") if isinstance(result.get("result_marker"), dict) else send_result.get("result_marker")
        return dict(marker) if isinstance(marker, dict) else {}


    def yoagent_job_result_source_from_result(self, result: dict[str, Any]) -> str:
        send_result = result.get("send") if isinstance(result.get("send"), dict) else {}
        return str(result.get("result_source") or send_result.get("result_source") or result.get("source") or "")


    def sanitize_yoagent_job(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        job_id = str(value.get("id") or "").strip()
        if not job_id:
            return None
        status = str(value.get("status") or "queued")
        if status not in {"queued", "pending_confirmation", "fired", "cancelled", "failed", "timed_out"}:
            status = "queued"
        target = value.get("target") if isinstance(value.get("target"), dict) else {}
        predicate = value.get("predicate") if isinstance(value.get("predicate"), dict) else {}
        action = value.get("action") if isinstance(value.get("action"), dict) else {}
        result = value.get("result") if isinstance(value.get("result"), dict) else {}
        prompt = truncate_text(str(value.get("prompt") or self.deps.yoagent_job_prompt_text(action)), YOAGENT_ACTION_TEXT_LIMIT)
        result_marker = value.get("result_marker") if isinstance(value.get("result_marker"), dict) else self.deps.yoagent_job_result_marker_from_result(result)
        job = {
            "id": job_id,
            "job_id": str(value.get("job_id") or job_id),
            "type": str(value.get("type") or "notify_session_idle"),
            "target": dict(target),
            "predicate": dict(predicate),
            "action": dict(action),
            "transport": str(value.get("transport") or self.deps.yoagent_job_transport_id(target, action, result)),
            "prompt": prompt,
            "prompt_preview": redacted_action_preview(prompt),
            "public_text": redacted_action_text(prompt, 240),
            "started_at": str(value.get("started_at") or ""),
            "result_marker": dict(result_marker),
            "result_source": str(value.get("result_source") or self.deps.yoagent_job_result_source_from_result(result)),
            "created_at": str(value.get("created_at") or datetime.now(timezone.utc).isoformat()),
            "created_ts": self.float_value(value.get("created_ts"), time.time()),
            "created_by": str(value.get("created_by") or "yoagent"),
            "locale": normalize_locale(value.get("locale")),
            "status": status,
            "last_observed_state": dict(value.get("last_observed_state") if isinstance(value.get("last_observed_state"), dict) else {}),
            "timeout_at": str(value.get("timeout_at") or ""),
            "timeout_ts": self.float_value(value.get("timeout_ts"), 0.0),
            "confirm_required": bool(value.get("confirm_required")),
            "confirmed_at": str(value.get("confirmed_at") or ""),
            "idempotency_key": str(value.get("idempotency_key") or ""),
            "audit_event_ids": [str(item) for item in value.get("audit_event_ids", []) if item],
        }
        for key in ("fired_at", "cancelled_at", "failed_at", "timed_out_at", "result", "error"):
            if key in value:
                job[key] = value[key]
        return job


    def load_yoagent_jobs(self) -> dict[str, dict[str, Any]]:
        raw = self.deps.read_yolomux_state().get(YOAGENT_JOBS_STATE_KEY, {})
        items = raw.values() if isinstance(raw, dict) else raw if isinstance(raw, list) else []
        jobs: dict[str, dict[str, Any]] = {}
        for item in items:
            job = self.deps.sanitize_yoagent_job(item)
            if job:
                jobs[str(job["id"])] = job
        return jobs


    def persist_yoagent_jobs_locked(self) -> None:
        jobs = dict(sorted(self.yoagent_jobs.items(), key=lambda item: float(item[1].get("created_ts") or 0), reverse=True)[:YOAGENT_JOB_MAX_ITEMS])
        self.yoagent_jobs = jobs
        self.deps.update_yolomux_state({YOAGENT_JOBS_STATE_KEY: jobs})


    def publish_yoagent_jobs_changed(self, reason: str, job: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"reason": reason}
        if job:
            payload["job"] = self.deps.public_yoagent_job(job)
            notification = job.get("notification") if isinstance(job.get("notification"), dict) else {}
            if notification:
                payload["notification"] = notification
        self.publish_client_event("yoagent_jobs_changed", payload, trigger=reason, cache="ready")


    def public_yoagent_job(self, job: dict[str, Any]) -> dict[str, Any]:
        public = copy.deepcopy(job)
        prompt = str(public.get("prompt") or "")
        if prompt:
            public["prompt_preview"] = redacted_action_preview(prompt)
            public["prompt"] = redacted_action_text(prompt, 240)
            public["public_text"] = redacted_action_text(prompt, 240)
        action = public.get("action") if isinstance(public.get("action"), dict) else {}
        if "text" in action:
            action["text_preview"] = redacted_action_preview(str(action.get("text") or ""))
            action["text"] = redacted_action_text(str(action.get("text") or ""), 240)
        public["action"] = action
        return public


    def public_yoagent_action_preview(self, preview: dict[str, Any]) -> dict[str, Any]:
        public = copy.deepcopy(preview)
        if "text" in public:
            public["text_preview"] = redacted_action_preview(str(public.get("text") or ""))
            public["text"] = redacted_action_text(str(public.get("text") or ""), YOAGENT_ACTION_TEXT_LIMIT)
        screen = public.get("screen") if isinstance(public.get("screen"), dict) else {}
        if "detected_text" in screen:
            detected = str(screen.get("detected_text") or "")
            screen["detected_text_preview"] = redacted_action_preview(detected)
            screen["detected_text"] = redacted_action_text(detected, 240)
            public["screen"] = screen
        handoff = public.get("handoff") if isinstance(public.get("handoff"), dict) else {}
        if "instruction" in handoff:
            handoff["instruction"] = redacted_action_text(str(handoff.get("instruction") or ""), YOAGENT_ACTION_TEXT_LIMIT)
        if handoff:
            public["handoff"] = handoff
        return public


    def yoagent_jobs_payload(self) -> tuple[dict[str, Any], HTTPStatus]:
        with self.yoagent_job_lock:
            jobs = [self.deps.public_yoagent_job(job) for job in self.yoagent_jobs.values()]
        jobs.sort(key=lambda item: float(item.get("created_ts") or 0), reverse=True)
        return {"ok": True, "jobs": jobs}, HTTPStatus.OK


    def yoagent_job_idempotency_key(self, job_type: str, target: dict[str, Any], predicate: dict[str, Any], action: dict[str, Any]) -> str:
        signature = self.client_event_payload_signature({
            "type": job_type,
            "target": target,
            "predicate": predicate,
            "action": action,
        })
        return hashlib.sha256(signature.encode("utf-8", errors="replace")).hexdigest()


    def yoagent_job_spec_from_payload(self, payload: dict[str, Any]) -> tuple[dict[str, Any], HTTPStatus]:
        locale = normalize_locale(payload.get("locale"))
        raw_type = str(payload.get("type") or payload.get("kind") or "").strip()
        job_type = raw_type or "notify_session_idle"
        target_payload = payload.get("target") if isinstance(payload.get("target"), dict) else {}
        action_payload = payload.get("action") if isinstance(payload.get("action"), dict) else {}
        session = str(payload.get("session") or target_payload.get("session") or action_payload.get("session") or "").strip()
        if job_type in {
            "notify_when_idle",
            "notify_session",
            "notify_session_idle",
            "notify_session_needs_input",
            "notify_needs_input",
            "notify_session_blocked",
            "notify_blocked",
            "notify_session_done_after_working",
            "notify_done_after_working",
        }:
            if job_type in {"notify_session_needs_input", "notify_needs_input"}:
                job_type = "notify_session_needs_input"
                predicate_type = "session_needs_input"
                message_key = "yoagent.job.notification.needsInput"
            elif job_type in {"notify_session_blocked", "notify_blocked"}:
                job_type = "notify_session_blocked"
                predicate_type = "session_blocked"
                message_key = "yoagent.job.notification.blocked"
            elif job_type in {"notify_session_done_after_working", "notify_done_after_working"}:
                job_type = "notify_session_done_after_working"
                predicate_type = "session_done_after_working"
                message_key = "yoagent.job.notification.doneAfterWorking"
            else:
                job_type = "notify_session_idle"
                predicate_type = "session_idle"
                message_key = "yoagent.job.notification.idle"
            if not session:
                diagnostic = "missing target session"
                return user_message_payload("yoagent.error.targetSessionRequired", diagnostic), HTTPStatus.BAD_REQUEST
            unknown = self.require_known_session(session)
            if unknown:
                unknown_payload, unknown_status = unknown
                diagnostic = str(unknown_payload.get("error") or f"unknown session: {session}")
                return {**unknown_payload, **user_message_payload("yoagent.error.unknownSession", diagnostic, session=session)}, unknown_status
            target = {"session": session}
            predicate = {"type": predicate_type, "quiet_seconds": self.float_value(payload.get("quiet_seconds"), YOAGENT_JOB_IDLE_QUIET_SECONDS)}
            action = yoagent_job_default_action(message_key, {"session": session}, payload.get("message"))
        elif job_type in {"notify_all_idle", "all_idle_summary"}:
            roster = [str(item) for item in (payload.get("roster") if isinstance(payload.get("roster"), list) else self.sessions) if str(item)]
            missing = [item for item in roster if item not in self.sessions]
            if missing:
                diagnostic = "unknown sessions in roster"
                return {"sessions": missing, **user_message_payload("yoagent.error.unknownSessions", diagnostic, sessions=", ".join(missing))}, HTTPStatus.NOT_FOUND
            target = {"roster": roster}
            predicate = {"type": "all_idle", "quiet_seconds": self.float_value(payload.get("quiet_seconds"), YOAGENT_JOB_IDLE_QUIET_SECONDS)}
            action = yoagent_job_default_action("yoagent.job.notification.allIdle", {}, payload.get("message"))
        elif job_type in {"wait_then_send", "wait_then_run"}:
            job_type = "wait_then_send"
            if not session:
                diagnostic = "missing target session"
                return user_message_payload("yoagent.error.targetSessionRequired", diagnostic), HTTPStatus.BAD_REQUEST
            unknown = self.require_known_session(session)
            if unknown:
                unknown_payload, unknown_status = unknown
                diagnostic = str(unknown_payload.get("error") or f"unknown session: {session}")
                return {**unknown_payload, **user_message_payload("yoagent.error.unknownSession", diagnostic, session=session)}, unknown_status
            text = truncate_text(str(payload.get("text") or action_payload.get("text") or "").strip(), YOAGENT_ACTION_TEXT_LIMIT)
            if not text:
                diagnostic = "missing prompt text"
                return {"session": session, **user_message_payload("yoagent.error.promptTextRequired", diagnostic)}, HTTPStatus.BAD_REQUEST
            target = {"session": session}
            predicate = {"type": "session_idle", "quiet_seconds": self.float_value(payload.get("quiet_seconds"), YOAGENT_JOB_IDLE_QUIET_SECONDS)}
            return_result = action_payload["return_result"] if "return_result" in action_payload else payload.get("return_result", True)
            action = {"type": "send_prompt", "session": session, "text": text, "submit": action_payload.get("submit") is not False, "return_result": bool(return_result)}
        else:
            diagnostic = f"unsupported YO!agent job type: {job_type}"
            return user_message_payload("yoagent.error.unsupportedJobType", diagnostic, type=job_type), HTTPStatus.BAD_REQUEST
        return {"type": job_type, "target": target, "predicate": predicate, "action": action, "locale": locale}, HTTPStatus.OK


    def create_yoagent_job(self, payload: dict[str, Any]) -> tuple[dict[str, Any], HTTPStatus]:
        spec, status = self.deps.yoagent_job_spec_from_payload(payload)
        if status != HTTPStatus.OK:
            return spec, status
        job_type = str(spec["type"])
        target = spec["target"]
        predicate = spec["predicate"]
        action = spec["action"]
        locale = str(spec.get("locale") or normalize_locale(payload.get("locale")))
        risk_labels = self.deps.yoagent_action_risk_labels(str(action.get("text") or "")) if action.get("type") == "send_prompt" else []
        if risk_labels:
            action["risk_labels"] = risk_labels
        confirm_required = bool(payload.get("confirm_required") or payload.get("requires_confirmation") or risk_labels)
        idempotency_key = str(payload.get("idempotency_key") or self.deps.yoagent_job_idempotency_key(job_type, target, predicate, action))
        with self.yoagent_job_lock:
            for existing in self.yoagent_jobs.values():
                if existing.get("idempotency_key") == idempotency_key and existing.get("status") in {"queued", "pending_confirmation"}:
                    return {"ok": False, "duplicate": True, "job": self.deps.public_yoagent_job(existing)}, HTTPStatus.CONFLICT
            now_ts = time.time()
            timeout_minutes = max(1.0, min(1440.0, self.float_value(payload.get("timeout_minutes"), YOAGENT_JOB_DEFAULT_TIMEOUT_MINUTES)))
            timeout_dt = datetime.now(timezone.utc) + timedelta(minutes=timeout_minutes)
            prompt = self.deps.yoagent_job_prompt_text(action)
            job_id = f"yj_{uuid.uuid4().hex[:16]}"
            job = {
                "id": job_id,
                "job_id": job_id,
                "type": job_type,
                "target": target,
                "predicate": predicate,
                "action": action,
                "transport": self.deps.yoagent_job_transport_id(target, action),
                "prompt": prompt,
                "prompt_preview": redacted_action_preview(prompt),
                "public_text": redacted_action_text(prompt, 240),
                "started_at": "",
                "result_marker": {},
                "result_source": "",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "created_ts": now_ts,
                "created_by": "yoagent",
                "locale": locale,
                "status": "pending_confirmation" if confirm_required else "queued",
                "last_observed_state": {},
                "timeout_at": timeout_dt.isoformat(),
                "timeout_ts": now_ts + timeout_minutes * 60.0,
                "confirm_required": confirm_required,
                "confirmed_at": "" if confirm_required else datetime.now(timezone.utc).isoformat(),
                "idempotency_key": idempotency_key,
                "audit_event_ids": [],
            }
            self.yoagent_jobs[job["id"]] = job
            self.deps.persist_yoagent_jobs_locked()
        event = self.log_event(
            str(target.get("session") or ""),
            "yoagent_job_created",
            f"YO!agent job created: {job_type}",
            {
                "job_id": job["id"],
                "type": job_type,
                "target_session": target.get("session", ""),
                "roster": target.get("roster", []),
                "predicate": predicate.get("type", ""),
                "action": action.get("type", ""),
                "text_preview": redacted_action_preview(str(action.get("text") or "")),
                "risk_labels": risk_labels,
            },
            message_key="events.message.yoagent.jobCreated",
            message_params={"type": yoagent_job_type_descriptor(job_type)},
        )
        with self.yoagent_job_lock:
            current = self.yoagent_jobs.get(job["id"])
            if current is not None:
                current["audit_event_ids"].append(str(event.get("time") or ""))
                self.deps.persist_yoagent_jobs_locked()
                job = copy.deepcopy(current)
        self.deps.publish_yoagent_jobs_changed("yoagent_job_created", job)
        return {"ok": True, "job": self.deps.public_yoagent_job(job)}, HTTPStatus.OK


    def confirm_yoagent_job(self, job_id: str) -> tuple[dict[str, Any], HTTPStatus]:
        with self.yoagent_job_lock:
            job = self.yoagent_jobs.get(str(job_id))
            if not job:
                diagnostic = "job not found"
                return user_message_payload("yoagent.error.jobNotFound", diagnostic), HTTPStatus.NOT_FOUND
            if job.get("status") != "pending_confirmation":
                return {"ok": True, "job": self.deps.public_yoagent_job(job)}, HTTPStatus.OK
            job["status"] = "queued"
            job["confirmed_at"] = datetime.now(timezone.utc).isoformat()
            self.deps.persist_yoagent_jobs_locked()
            public = copy.deepcopy(job)
        self.log_event(
            str(public.get("target", {}).get("session") or ""),
            "yoagent_job_confirmed",
            f"YO!agent job confirmed: {job_id}",
            {"job_id": job_id},
            message_key="events.message.yoagent.jobConfirmed",
            message_params={"id": job_id},
        )
        self.deps.publish_yoagent_jobs_changed("yoagent_job_confirmed", public)
        self.client_watch_wake_event.set()
        return {"ok": True, "job": self.deps.public_yoagent_job(public)}, HTTPStatus.OK


    def cancel_yoagent_job(self, job_id: str) -> tuple[dict[str, Any], HTTPStatus]:
        with self.yoagent_job_lock:
            job = self.yoagent_jobs.get(str(job_id))
            if not job:
                diagnostic = "job not found"
                return user_message_payload("yoagent.error.jobNotFound", diagnostic), HTTPStatus.NOT_FOUND
            if job.get("status") in {"fired", "cancelled", "failed", "timed_out"}:
                return {"ok": True, "job": self.deps.public_yoagent_job(job)}, HTTPStatus.OK
            job["status"] = "cancelled"
            job["cancelled_at"] = datetime.now(timezone.utc).isoformat()
            self.deps.persist_yoagent_jobs_locked()
            public = copy.deepcopy(job)
        self.log_event(
            str(public.get("target", {}).get("session") or ""),
            "yoagent_job_cancelled",
            f"YO!agent job cancelled: {job_id}",
            {"job_id": job_id},
            message_key="events.message.yoagent.jobCancelled",
            message_params={"id": job_id},
        )
        self.deps.publish_yoagent_jobs_changed("yoagent_job_cancelled", public)
        return {"ok": True, "job": self.deps.public_yoagent_job(public)}, HTTPStatus.OK


    def cancel_yoagent_jobs_for_session(self, session: str) -> tuple[dict[str, Any], HTTPStatus]:
        target_session = str(session or "").strip()
        if not target_session:
            diagnostic = "missing target session"
            return {"ok": False, **user_message_payload("yoagent.error.targetSessionRequired", diagnostic)}, HTTPStatus.BAD_REQUEST
        unknown = self.require_known_session(target_session)
        if unknown:
            unknown_payload, unknown_status = unknown
            diagnostic = str(unknown_payload.get("error") or f"unknown session: {target_session}")
            return {**unknown_payload, **user_message_payload("yoagent.error.unknownSession", diagnostic, session=target_session)}, unknown_status
        cancelled: list[dict[str, Any]] = []
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.yoagent_job_lock:
            for job in self.yoagent_jobs.values():
                if job.get("status") not in {"queued", "pending_confirmation"}:
                    continue
                target = job.get("target") if isinstance(job.get("target"), dict) else {}
                action = job.get("action") if isinstance(job.get("action"), dict) else {}
                if str(target.get("session") or action.get("session") or "").strip() != target_session:
                    continue
                job["status"] = "cancelled"
                job["cancelled_at"] = now_iso
                cancelled.append(copy.deepcopy(job))
            if cancelled:
                self.deps.persist_yoagent_jobs_locked()
        public_jobs = [self.deps.public_yoagent_job(job) for job in cancelled]
        if cancelled:
            self.log_event(
                target_session,
                "yoagent_jobs_cancelled",
                f"YO!agent cancelled {len(cancelled)} job(s) for tmux session {target_session}",
                {"session": target_session, "count": len(cancelled), "job_ids": [job["id"] for job in cancelled]},
                message_key="events.message.yoagent.jobsCancelled",
                message_params={"session": target_session, "count": len(cancelled)},
            )
            self.publish_client_event("yoagent_jobs_changed", {"reason": "yoagent_jobs_cancelled_for_session", "session": target_session, "count": len(public_jobs), "jobs": public_jobs}, trigger="yoagent_jobs_cancelled_for_session", cache="ready")
        return {"ok": True, "session": target_session, "count": len(public_jobs), "jobs": public_jobs}, HTTPStatus.OK


    def yoagent_intent(self, payload: dict[str, Any]) -> tuple[dict[str, Any], HTTPStatus]:
        intent = payload.get("intent") if isinstance(payload.get("intent"), dict) else payload
        intent_type = str(intent.get("type") or "")
        if intent_type in {"send_prompt", "wait_then_send", "session_handoff"}:
            preview, status = self.deps.create_yoagent_action_preview(intent)
            risk = "mutating-send" if str(preview.get("status") or "") == "ready" else "waiting-target"
            return {"ok": status == HTTPStatus.OK, "intent": intent, "preview": preview, "risk": risk, "confirmation_required": bool(preview.get("requires_confirmation"))}, status
        if intent_type == "cancel_session_jobs":
            response, status = self.deps.cancel_yoagent_jobs_for_session(str(intent.get("session") or ""))
            return {"ok": status == HTTPStatus.OK, "intent": intent, **response}, status
        job, status = self.deps.yoagent_job_spec_from_payload(intent)
        return {"ok": status == HTTPStatus.OK, "intent": intent, "job_preview": job, "risk": "mutating-job" if (job.get("action") or {}).get("type") == "send_prompt" else "notify-only"}, status


    def yoagent_job_observed_state(self, job: dict[str, Any]) -> dict[str, Any]:
        predicate = job.get("predicate") if isinstance(job.get("predicate"), dict) else {}
        target = job.get("target") if isinstance(job.get("target"), dict) else {}
        predicate_type = str(predicate.get("type") or "")
        if predicate_type == "all_idle":
            roster = [str(item) for item in target.get("roster", []) if str(item)]
            blockers: list[str] = []
            states: dict[str, str] = {}
            for session in roster:
                current, status = self.deps.yoagent_action_target(session)
                if status != HTTPStatus.OK:
                    states[session] = "missing"
                    blockers.append(session)
                    continue
                screen = current.get("screen") if isinstance(current.get("screen"), dict) else {}
                state_key = str(screen.get("key") or "idle")
                states[session] = state_key
                if state_key not in YOAGENT_ACTION_ACCEPTING_SCREEN_KEYS:
                    blockers.append(session)
            return {"ready": not blockers and bool(roster), "state": "all_idle" if not blockers else "waiting", "states": states, "blockers": blockers}
        session = str(target.get("session") or "").strip()
        current, status = self.deps.yoagent_action_target(session)
        if status != HTTPStatus.OK:
            return {"ready": False, "state": "missing", "error": current.get("error") or status.phrase}
        accepting, acceptance_text = self.deps.yoagent_action_acceptance({**current, "locale": normalize_locale(job.get("locale"))})
        screen = current.get("screen") if isinstance(current.get("screen"), dict) else {}
        prompt = current.get("prompt") if isinstance(current.get("prompt"), dict) else {}
        state_key = str(screen.get("key") or ("idle" if accepting else "waiting"))
        if predicate_type == "session_needs_input":
            ready = state_key in YOAGENT_JOB_NEEDS_INPUT_SCREEN_KEYS or str(screen.get("attention_kind") or "") == "question"
        elif predicate_type == "session_blocked":
            ready = bool(prompt.get("visible")) or state_key in YOAGENT_JOB_BLOCKED_SCREEN_KEYS or str(screen.get("attention_kind") or "") == "approval"
        elif predicate_type == "session_done_after_working":
            ready = accepting
        else:
            ready = accepting if predicate_type in {"session_idle", "session_done"} else False
        return {
            "ready": ready,
            "state": state_key,
            "acceptance_text": acceptance_text,
            "attention_kind": str(screen.get("attention_kind") or ""),
            "question_text": str(screen.get("question_text") or prompt.get("question_text") or screen.get("text") or ""),
            "target": current,
        }


    def update_yoagent_job_observation(self, job_id: str, observed: dict[str, Any], now: float) -> tuple[dict[str, Any] | None, bool]:
        with self.yoagent_job_lock:
            job = self.yoagent_jobs.get(job_id)
            if not job or job.get("status") != "queued":
                return None, False
            predicate = job.get("predicate") if isinstance(job.get("predicate"), dict) else {}
            quiet_seconds = max(0.0, self.float_value(predicate.get("quiet_seconds"), YOAGENT_JOB_IDLE_QUIET_SECONDS))
            previous = job.get("last_observed_state") if isinstance(job.get("last_observed_state"), dict) else {}
            state = str(observed.get("state") or "")
            ready = bool(observed.get("ready"))
            seen_working = bool(previous.get("seen_working")) or state == "working"
            if previous.get("state") != state or bool(previous.get("ready")) != ready:
                since = now
            else:
                since = self.float_value(previous.get("since_ts"), now)
            job["last_observed_state"] = {
                "state": state,
                "ready": ready,
                "since_ts": since,
                "observed_ts": now,
                "blockers": observed.get("blockers", []),
                "states": observed.get("states", {}),
                "acceptance_text": observed.get("acceptance_text", ""),
                "attention_kind": observed.get("attention_kind", ""),
                "question_text": observed.get("question_text", ""),
                "seen_working": seen_working,
            }
            self.deps.persist_yoagent_jobs_locked()
            predicate_type = str(predicate.get("type") or "")
            ready_after_working = predicate_type != "session_done_after_working" or seen_working
            return copy.deepcopy(job), ready and ready_after_working and now - since >= quiet_seconds


    def complete_yoagent_job(self, job_id: str, status: str, result: dict[str, Any]) -> dict[str, Any] | None:
        with self.yoagent_job_lock:
            job = self.yoagent_jobs.get(job_id)
            if not job:
                return None
            job["status"] = status
            timestamp_key = "fired_at" if status == "fired" else "failed_at" if status == "failed" else "timed_out_at"
            timestamp = datetime.now(timezone.utc).isoformat()
            job[timestamp_key] = timestamp
            if status == "fired" and not job.get("started_at"):
                job["started_at"] = timestamp
            if result:
                job["result"] = result
                transport = self.deps.yoagent_job_transport_id(
                    job.get("target") if isinstance(job.get("target"), dict) else {},
                    job.get("action") if isinstance(job.get("action"), dict) else {},
                    result,
                )
                if transport:
                    job["transport"] = transport
                marker = self.deps.yoagent_job_result_marker_from_result(result)
                if marker:
                    job["result_marker"] = marker
                result_source = self.deps.yoagent_job_result_source_from_result(result)
                if result_source:
                    job["result_source"] = result_source
            self.deps.persist_yoagent_jobs_locked()
            return copy.deepcopy(job)


    def fire_yoagent_job(self, job: dict[str, Any], observed: dict[str, Any]) -> None:
        job_id = str(job.get("id") or "")
        action = job.get("action") if isinstance(job.get("action"), dict) else {}
        action_type = str(action.get("type") or "notify_user")
        session = str((job.get("target") if isinstance(job.get("target"), dict) else {}).get("session") or action.get("session") or "")
        locale = normalize_locale(job.get("locale"))
        if action_type == "notify_user":
            body_fields = yoagent_job_message_fields(
                "body",
                action,
                default_key="yoagent.job.notification.fired",
                default_params={"id": job_id},
            )
            result_fields = yoagent_job_message_fields(
                "message",
                action,
                default_key="yoagent.job.notification.fired",
                default_params={"id": job_id},
            )
            notification = {
                **message_fields("title", "brand.tab.agent", yoagent_text("en", "brand.tab.agent")),
                **body_fields,
                "session": session,
            }
            completed = self.deps.complete_yoagent_job(job_id, "fired", result_fields)
            if completed:
                completed["notification"] = notification
                self.log_event(
                    session,
                    "yoagent_job_fired",
                    str(result_fields.get("message") or ""),
                    {"job_id": job_id, "type": job.get("type"), "state": observed.get("state", "")},
                    message_key=str(result_fields.get("message_key") or ""),
                    message_params=result_fields.get("message_params") if isinstance(result_fields.get("message_params"), dict) else {},
                )
                self.deps.publish_yoagent_jobs_changed("yoagent_job_fired", completed)
            return
        if action_type == "send_prompt":
            intent = {
                "type": "send_prompt",
                "session": str(action.get("session") or session),
                "text": str(action.get("text") or ""),
                "submit": action.get("submit") is not False,
                "return_result": bool(action.get("return_result")),
                "locale": locale,
            }
            preview, preview_status = self.deps.create_yoagent_action_preview(intent)
            if preview_status == HTTPStatus.OK and preview.get("status") == "ready":
                result, result_status = self.deps.execute_yoagent_send_action({"preview_id": preview.get("id")}, persist_result=True, start_result_watch=bool(intent.get("return_result")))
                completed = self.deps.complete_yoagent_job(job_id, "fired" if result_status == HTTPStatus.OK else "failed", {"action": preview, "send": result, "status": int(result_status)})
                if completed:
                    self.log_event(
                        str(intent.get("session") or ""),
                        "yoagent_job_fired",
                        f"YO!agent job sent prompt to {intent.get('session')}",
                        {"job_id": job_id, "status": int(result_status), "text_preview": redacted_action_preview(str(intent.get("text") or ""))},
                        message_key="events.message.yoagent.jobPromptSent",
                        message_params={"session": str(intent.get("session") or "")},
                    )
                    self.deps.publish_yoagent_jobs_changed("yoagent_job_fired", completed)
                return
            diagnostic = str(preview.get("error") or preview.get("acceptance_text") or "target is not ready")
            failure = {"action": preview, **user_message_payload("yoagent.error.targetNotReady", diagnostic)}
            failed = self.deps.complete_yoagent_job(job_id, "failed", failure)
            if failed:
                reason = preview.get("user_message") if isinstance(preview.get("user_message"), dict) else message_descriptor(
                    "yoagent.error.targetNotReady",
                    diagnostic,
                )
                self.log_event(
                    str(intent.get("session") or ""),
                    "yoagent_job_failed",
                    f"YO!agent job failed: {reason.get('fallback') or diagnostic}",
                    {"job_id": job_id, "diagnostic": diagnostic},
                    message_key="yoagent.job.notification.failed",
                    message_params={"id": job_id, "reason": reason},
                )
                self.deps.publish_yoagent_jobs_changed("yoagent_job_failed", failed)


    def poll_yoagent_jobs_once(self) -> list[str]:
        now = time.time()
        fired: list[str] = []
        with self.yoagent_job_lock:
            jobs = [copy.deepcopy(job) for job in self.yoagent_jobs.values() if job.get("status") == "queued"]
        for job in jobs:
            job_id = str(job.get("id") or "")
            locale = normalize_locale(job.get("locale"))
            if self.float_value(job.get("timeout_ts"), 0.0) and now >= self.float_value(job.get("timeout_ts"), 0.0):
                timed_out = self.deps.complete_yoagent_job(job_id, "timed_out", {"reason": "timeout"})
                if timed_out:
                    timed_out["notification"] = {
                        **message_fields("title", "brand.tab.agent", yoagent_text("en", "brand.tab.agent")),
                        **message_fields(
                            "body",
                            "yoagent.job.notification.timedOut",
                            yoagent_text("en", "yoagent.job.notification.timedOut", id=job_id),
                            {"id": job_id},
                        ),
                        "session": str((timed_out.get("target") or {}).get("session") or ""),
                    }
                    self.log_event(
                        str((timed_out.get("target") or {}).get("session") or ""),
                        "yoagent_job_timed_out",
                        f"YO!agent job timed out: {job_id}",
                        {"job_id": job_id},
                        message_key="yoagent.job.notification.timedOut",
                        message_params={"id": job_id},
                    )
                    self.deps.publish_yoagent_jobs_changed("yoagent_job_timed_out", timed_out)
                continue
            observed = self.deps.yoagent_job_observed_state(job)
            if str(observed.get("state") or "") == "missing":
                diagnostic = str(observed.get("error") or "target session is missing")
                failed = self.deps.complete_yoagent_job(
                    job_id,
                    "failed",
                    {"observed": observed, **user_message_payload("yoagent.error.targetSessionMissing", diagnostic)},
                )
                if failed:
                    reason_descriptor = message_descriptor(
                        "yoagent.error.targetSessionMissing",
                        yoagent_text("en", "yoagent.error.targetSessionMissing"),
                    )
                    failed["notification"] = {
                        **message_fields("title", "brand.tab.agent", yoagent_text("en", "brand.tab.agent")),
                        **message_fields(
                            "body",
                            "yoagent.job.notification.failed",
                            yoagent_text("en", "yoagent.job.notification.failed", id=job_id, reason=reason_descriptor["fallback"]),
                            {"id": job_id, "reason": reason_descriptor},
                        ),
                        "session": str((failed.get("target") or {}).get("session") or ""),
                    }
                    self.log_event(
                        str((failed.get("target") or {}).get("session") or ""),
                        "yoagent_job_failed",
                        yoagent_text(
                            "en",
                            "yoagent.job.notification.failed",
                            id=job_id,
                            reason=reason_descriptor["fallback"],
                        ),
                        {"job_id": job_id, "diagnostic": diagnostic},
                        message_key="yoagent.job.notification.failed",
                        message_params={"id": job_id, "reason": reason_descriptor},
                    )
                    self.deps.publish_yoagent_jobs_changed("yoagent_job_failed", failed)
                continue
            current, should_fire = self.deps.update_yoagent_job_observation(job_id, observed, now)
            if current and should_fire:
                self.deps.fire_yoagent_job(current, observed)
                fired.append(job_id)
        return fired


    def yoagent_wait_regarding_text(self, text: Any, fallback: str) -> str:
        preview = redacted_action_preview(" ".join(str(text or "").split()), 90).strip(" .")
        return preview or fallback


    def yoagent_action_wait_label(self, preview: dict[str, Any]) -> str:
        locale = normalize_locale(preview.get("locale"))
        target = preview.get("target") if isinstance(preview.get("target"), dict) else {}
        session = str(preview.get("session") or target.get("session") or "").strip()
        handoff = preview.get("handoff") if isinstance(preview.get("handoff"), dict) else {}
        target_session = str(handoff.get("session") or "").strip()
        if target_session:
            source_regarding = self.deps.yoagent_wait_regarding_text(preview.get("text"), yoagent_text(locale, "yoagent.waiting.currentRequest"))
            target_regarding = self.deps.yoagent_wait_regarding_text(handoff.get("instruction"), yoagent_text(locale, "yoagent.waiting.nextRequest"))
            return yoagent_text(
                locale,
                "yoagent.waiting.handoff",
                source=session,
                sourceRegarding=source_regarding,
                target=target_session,
                targetRegarding=target_regarding,
            )
        return yoagent_text(locale, "yoagent.waiting.session", session=f"`{session}`")


    def register_yoagent_action_wait(self, watch_id: str, preview: dict[str, Any], marker: dict[str, Any]) -> dict[str, Any]:
        target = preview.get("target") if isinstance(preview.get("target"), dict) else {}
        session = str(preview.get("session") or target.get("session") or "").strip()
        handoff = preview.get("handoff") if isinstance(preview.get("handoff"), dict) else {}
        pending = {
            "id": watch_id,
            "session": session,
            "label": self.deps.yoagent_action_wait_label(preview),
            "started_ts": time.time(),
            "wait_seconds": YOAGENT_ACTION_RESULT_WAIT_SECONDS,
            "transcript": str(marker.get("transcript") or ""),
        }
        if handoff:
            pending["handoff"] = {
                "source_session": str(handoff.get("source_session") or session),
                "session": str(handoff.get("session") or ""),
                "source_regarding": self.deps.yoagent_wait_regarding_text(preview.get("text"), yoagent_text(normalize_locale(preview.get("locale")), "yoagent.waiting.currentRequest")),
                "target_regarding": self.deps.yoagent_wait_regarding_text(handoff.get("instruction"), yoagent_text(normalize_locale(preview.get("locale")), "yoagent.waiting.nextRequest")),
            }
        with self.yoagent_action_lock:
            self.yoagent_action_waits[watch_id] = pending
        self.publish_yoagent_conversation_changed("yoagent_wait_started")
        return pending


    def finish_yoagent_action_wait(self, watch_id: str | None, reason: str = "done") -> None:
        if not watch_id:
            return
        removed = False
        with self.yoagent_action_lock:
            removed = self.yoagent_action_waits.pop(watch_id, None) is not None
        if removed:
            self.publish_yoagent_conversation_changed(reason)


    def clear_yoagent_action_wait(self, watch_id: str) -> tuple[dict[str, Any], HTTPStatus]:
        clean_id = str(watch_id or "").strip()
        if not clean_id:
            diagnostic = "missing wait id"
            return {"ok": False, **user_message_payload("yoagent.error.waitIdRequired", diagnostic)}, HTTPStatus.BAD_REQUEST
        with self.yoagent_action_lock:
            exists = clean_id in self.yoagent_action_waits
        if not exists:
            return {
                "ok": False,
                "id": clean_id,
                "conversation": self.deps.yoagent_conversation_payload(),
                **user_message_payload("yoagent.error.waitNotFound", "wait not found"),
            }, HTTPStatus.NOT_FOUND
        self.deps.finish_yoagent_action_wait(clean_id, "yoagent_wait_cleared")
        return {
            "ok": True,
            "id": clean_id,
            "conversation": self.deps.yoagent_conversation_payload(),
        }, HTTPStatus.OK


    def yoagent_prompt_history(self, raw_history: Any, question: str) -> list[dict[str, str]]:
        persisted = yoagent_conversation.load_messages()
        source = persisted if persisted else raw_history if isinstance(raw_history, list) else []
        sanitized = [message for message in (yoagent_conversation.sanitize_message(item) for item in source) if message is not None]
        if sanitized and sanitized[-1].get("role") == "user":
            latest = " ".join(str(sanitized[-1].get("content") or "").split())
            if latest == question:
                sanitized = sanitized[:-1]
        history: list[dict[str, str]] = []
        for item in sanitized[-8:]:
            role = "user" if item.get("role") == "user" else "assistant"
            content = truncate_text(" ".join(str(item.get("content") or "").split()), 1000)
            if content:
                history.append({"role": role, "content": content})
        return history


    def yoagent_activity_payload(self, force: bool = False) -> dict[str, Any]:
        payload = dict(self.activity_summary_payload(session_scope="all", force=force))
        payload["yoagent_skills"] = self.yoagent_skills_payload()
        return payload


    def prune_yoagent_action_previews(self) -> None:
        cutoff = time.time() - YOAGENT_ACTION_PREVIEW_TTL_SECONDS
        with self.yoagent_action_lock:
            for preview_id, preview in list(self.yoagent_action_previews.items()):
                if float(preview.get("created_ts") or 0) < cutoff:
                    self.yoagent_action_previews.pop(preview_id, None)


    def yoagent_managed_target_metadata(self, session: str, agent: Any | None) -> dict[str, Any]:
        candidates = [str(session or "").strip()]
        if agent is not None:
            session_id = str(agent.session_id or "").strip()
            pane_target = str(agent.pane_target or "").strip()
            if session_id:
                candidates.append(session_id)
                candidates.append(f"{session}:{session_id}")
            if pane_target:
                candidates.append(pane_target)
                candidates.append(f"{session}:{pane_target}")
        for key in candidates:
            if key and key in self.yoagent_managed_targets:
                value = self.yoagent_managed_targets[key]
                return dict(value) if isinstance(value, dict) else {}
        return {}


    def yoagent_target_with_transport(self, target: dict[str, Any]) -> dict[str, Any]:
        transport_provider = self.yoagent_transports.first_available(target)
        return {
            **target,
            "transport": transport_provider.id,
            "transport_label": transport_provider.label,
            "transport_kind": transport_provider.kind,
            "transport_capabilities": list(transport_provider.capabilities),
        }


    def yoagent_action_target(self, session: str) -> tuple[dict[str, Any], HTTPStatus]:
        discovered, errors = self.deps.discover_sessions([session])
        info = discovered.get(session)
        if info is None:
            diagnostic = "session metadata unavailable"
            return {
                "session": session,
                "errors": errors,
                **user_message_payload("yoagent.error.sessionMetadataUnavailable", diagnostic, session=session),
            }, HTTPStatus.NOT_FOUND
        selected = info.selected_pane
        agent = None
        if selected:
            agent = next((item for item in info.agents if item.pane_target == selected.target), None)
        if agent is None:
            agent = next((item for item in info.agents if item.transcript), info.agents[0] if info.agents else None)
        target_pane = agent.pane_target if agent and agent.pane_target else (selected.target if selected else tmux_session_target(session))
        prompt, screen = self.deps.yoagent_action_pane_status(session, target_pane, discovered_sessions=discovered)
        target = {
            "session": session,
            "pane_target": target_pane,
            "pane_window": selected.window if selected else "",
            "pane_index": selected.pane if selected else "",
            "cwd": (agent.cwd if agent and agent.cwd else selected.current_path if selected else "") or "",
            "agent_kind": agent.kind if agent else "",
            "agent_session_id": agent.session_id if agent else "",
            "agent_model": agent.model if agent else "",
            "agent_transcript": agent.transcript if agent and agent.transcript else "",
            "prompt": prompt,
            "screen": screen,
            "errors": errors,
        }
        managed_metadata = self.deps.yoagent_managed_target_metadata(session, agent)
        if managed_metadata:
            target.update(managed_metadata)
            target["managed"] = managed_metadata.get("managed") is not False
        return self.deps.yoagent_target_with_transport(target), HTTPStatus.OK


    def yoagent_action_pane_status(self, session: str, target_pane: str, discovered_sessions: dict[str, SessionInfo] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
        infos = discovered_sessions
        if infos is None:
            infos, _errors = self.deps.discover_sessions([session])

        def prompt_classifier(prompt_target: str, visible_text: str, pane_text: str | None, prompt_source: str) -> dict[str, Any]:
            return self.deps.hybrid_approval_prompt_state(prompt_target, visible_text, pane_text, prompt_source=prompt_source)

        def screen_classifier(visible_text: str, pane_target: str | None) -> dict[str, Any]:
            return dict(self.deps.agent_screen_state(visible_text, pane_target=pane_target))

        state = classify_agent_pane(
            target_pane,
            session=session,
            discovered_sessions=infos,
            prompt_source=self.auto_approve_prompt_source(),
            include_composer=True,
            include_transcript_activity=True,
            capture_func=self.deps.tmux_capture_pane,
            capture_styled_func=self.deps.tmux_capture_pane_styled,
            prompt_classifier=prompt_classifier,
            screen_classifier=screen_classifier,
        )
        return self.deps.normalized_prompt_state(state.prompt), dict(state.screen)


    def yoagent_action_acceptance(self, target: dict[str, Any], locale: str = "") -> tuple[bool, str]:
        locale = normalize_locale(locale or target.get("locale"))
        agent_kind = str(target.get("agent_kind") or "").strip().lower()
        if agent_kind not in YOAGENT_ACTION_AGENT_KINDS:
            return False, yoagent_text(locale, "yoagent.action.acceptance.notAgent")
        if not str(target.get("pane_target") or "").strip():
            return False, yoagent_text(locale, "yoagent.action.acceptance.missingPane")
        prompt = target.get("prompt") if isinstance(target.get("prompt"), dict) else {}
        if prompt.get("visible"):
            return False, yoagent_text(locale, "yoagent.action.acceptance.approvalNotFresh")
        screen = target.get("screen") if isinstance(target.get("screen"), dict) else {}
        screen_key = str(screen.get("key") or "idle")
        if screen_key == "input-draft":
            return True, yoagent_text(locale, "yoagent.action.acceptance.draftClear")
        if screen_key in YOAGENT_ACTION_ACCEPTING_SCREEN_KEYS:
            return True, yoagent_text(locale, "yoagent.action.acceptance.accepting")
        if screen_key == "needs-input":
            return False, yoagent_text(locale, "yoagent.action.acceptance.needsInput")
        if screen_key == "working":
            return False, yoagent_text(locale, "yoagent.action.acceptance.working")
        if screen_key in {"approval", "needs-approval", "yolo-approval"}:
            return False, yoagent_text(locale, "yoagent.action.acceptance.approval")
        if screen_key in {"disconnected", "error"}:
            return False, str(screen.get("text") or yoagent_text(locale, "yoagent.action.acceptance.unreachable"))
        return False, yoagent_text(locale, "yoagent.action.acceptance.notAccepting", state=screen_key)


    def yoagent_prompt_answer_options_text(self, target: dict[str, Any]) -> str:
        screen = target.get("screen") if isinstance(target.get("screen"), dict) else {}
        prompt = target.get("prompt") if isinstance(target.get("prompt"), dict) else {}
        options = screen.get("options") if isinstance(screen.get("options"), list) else prompt.get("options") if isinstance(prompt.get("options"), list) else []
        rows = []
        for index, option in enumerate(options[:8], start=1):
            text = str(option.get("text") if isinstance(option, dict) else option).strip()
            if text:
                rows.append(f"{index}. {text}")
        return "; ".join(rows)


    def yoagent_target_prompt_visible(self, target: dict[str, Any]) -> bool:
        prompt = target.get("prompt") if isinstance(target.get("prompt"), dict) else {}
        screen = target.get("screen") if isinstance(target.get("screen"), dict) else {}
        screen_key = str(screen.get("key") or "").strip()
        return bool(prompt.get("visible")) or screen_key in {"approval", "needs-approval", "yolo-approval", "needs-input"}


    def yoagent_prompt_answer_error_prefix(self, target: dict[str, Any], locale: str = "en") -> str:
        prompt = target.get("prompt") if isinstance(target.get("prompt"), dict) else {}
        screen = target.get("screen") if isinstance(target.get("screen"), dict) else {}
        screen_key = str(screen.get("key") or "").strip()
        if screen_key == "needs-input":
            return yoagent_text(locale, "yoagent.action.prompt.needsInputPrefix")
        if bool(prompt.get("visible")) or screen_key in {"approval", "needs-approval", "yolo-approval"}:
            return yoagent_text(locale, "yoagent.action.acceptance.approval")
        return yoagent_text(locale, "yoagent.action.prompt.genericPrefix")


    def yoagent_prompt_answer_plan(self, text: str, target: dict[str, Any], locale: str = "en") -> dict[str, Any]:
        prompt = target.get("prompt") if isinstance(target.get("prompt"), dict) else {}
        screen = target.get("screen") if isinstance(target.get("screen"), dict) else {}
        if not self.yoagent_target_prompt_visible(target):
            return {"ready": False, "error": yoagent_text(locale, "yoagent.action.prompt.notVisible")}
        raw_text = " ".join(str(text or "").strip().split())
        normalized = raw_text.lower().strip(" .")
        selected = int(screen.get("selected_option") or prompt.get("selected_option") or 0)
        options = screen.get("options") if isinstance(screen.get("options"), list) else prompt.get("options") if isinstance(prompt.get("options"), list) else []
        option = 0
        key = ""
        number_match = re.fullmatch(r"(?:option\s*)?(\d{1,2})", normalized)
        if number_match:
            option = int(number_match.group(1))
        elif normalized in {"enter", "return", "press enter", "select", "selected", "current"}:
            key = "Enter"
            option = selected
        elif normalized in {"escape", "esc", "cancel", "press escape"}:
            key = "Escape"
        elif normalized in {"yes", "y", "approve", "allow"}:
            option = 1
        elif normalized in {"no", "n", "decline", "deny"}:
            option = 2
        else:
            options_text = self.yoagent_prompt_answer_options_text(target)
            suffix = yoagent_text(locale, "yoagent.action.prompt.optionsSuffix", options=options_text) if options_text else ""
            return {"ready": False, "error": yoagent_text(locale, "yoagent.action.prompt.responseRequired", prefix=self.yoagent_prompt_answer_error_prefix(target, locale), options=suffix)}
        if key == "Escape":
            return {"ready": True, "key": key, "selected_option": selected, "text": raw_text}
        if option <= 0:
            return {"ready": False, "error": yoagent_text(locale, "yoagent.action.prompt.noSelected")}
        if options and option > len(options):
            return {"ready": False, "error": yoagent_text(locale, "yoagent.action.prompt.optionUnavailable", count=len(options), option=option)}
        if selected <= 0:
            options_text = self.yoagent_prompt_answer_options_text(target)
            suffix = yoagent_text(locale, "yoagent.action.prompt.optionsSuffix", options=options_text) if options_text else ""
            return {"ready": False, "error": yoagent_text(locale, "yoagent.action.prompt.selectedMissing", prefix=self.yoagent_prompt_answer_error_prefix(target, locale), options=suffix)}
        return {"ready": True, "option": option, "selected_option": selected, "text": raw_text}


    def execute_yoagent_prompt_answer(self, preview: dict[str, Any], current: dict[str, Any]) -> tuple[dict[str, Any], HTTPStatus]:
        locale = normalize_locale(preview.get("locale"))
        plan = self.yoagent_prompt_answer_plan(str(preview.get("text") or ""), current, locale)
        preview_id = str(preview.get("id") or "")
        session = str(preview.get("session") or current.get("session") or "")
        pane_target = str(current.get("pane_target") or "").strip()
        if not plan.get("ready"):
            diagnostic = str(plan.get("error") or "prompt answer is not available")
            return {
                "preview_id": preview_id,
                "session": session,
                "sent": False,
                "reason_code": "prompt-answer-unavailable",
                **user_message_payload("yoagent.error.promptAnswerUnavailable", diagnostic),
            }, HTTPStatus.CONFLICT
        if not pane_target:
            diagnostic = "target pane is missing"
            return {
                "preview_id": preview_id,
                "session": session,
                "sent": False,
                "reason_code": "disconnected",
                **user_message_payload("yoagent.action.acceptance.missingPane", diagnostic),
            }, HTTPStatus.CONFLICT
        key = str(plan.get("key") or "")
        if key == "Escape":
            result = tmux_run("send-keys", "-t", pane_target, "Escape", check=False, timeout=5.0)
            if result.returncode != 0:
                diagnostic = (result.stderr or result.stdout or "tmux send-keys failed").strip()
                return {
                    "preview_id": preview_id,
                    "session": session,
                    "sent": False,
                    "reason_code": "tmux-send-failed",
                    **user_message_payload("yoagent.error.tmuxSendFailed", diagnostic),
                }, HTTPStatus.INTERNAL_SERVER_ERROR
            answer = yoagent_text(locale, "yoagent.action.answer.cancelled", session=session)
            return {"ok": True, "preview_id": preview_id, "session": session, "sent": True, "prompt_answer": True, "key": "Escape", "answer": answer}, HTTPStatus.OK
        option = int(plan.get("option") or 0)
        selected = int(plan.get("selected_option") or 0)
        tmux_move_to_option(pane_target, option, selected)
        visible_text = self.deps.tmux_capture_pane(pane_target, visible_only=True) or ""
        confirmed = selected_prompt_option(visible_text)
        if confirmed != option:
            diagnostic = f"prompt highlight is {confirmed or 'none'}, expected {option}; I did not press Enter"
            return {
                "preview_id": preview_id,
                "session": session,
                "sent": False,
                "reason_code": "prompt-answer-unverified",
                **user_message_payload(
                    "yoagent.error.promptAnswerUnverified",
                    diagnostic,
                    actual=confirmed or yoagent_text(locale, "common.unknown"),
                    expected=option,
                ),
            }, HTTPStatus.CONFLICT
        tmux_send_enter(pane_target)
        answer = yoagent_text(locale, "yoagent.action.answer.selected", session=session, option=option)
        return {"ok": True, "preview_id": preview_id, "session": session, "sent": True, "prompt_answer": True, "option": option, "answer": answer}, HTTPStatus.OK


    def yoagent_action_risk_labels(self, text: str) -> list[str]:
        value = str(text or "")
        checks = [
            ("secret-like-text", r"(?i)\b(?:token|secret|password|api[_-]?key)\s*=\s*\S+"),
            ("credential-path", r"(?i)(~?/\.config/gitlab-token|~?/\.cache/huggingface/token|~?/\.docker/config\.json|~?/\.ngc/config)"),
            ("recursive-delete", r"(?i)\brm\s+-[^\n]*r[^\n]*f\b"),
            ("hard-reset", r"(?i)\bgit\s+reset\s+--hard\b"),
            ("broad-pkill", r"(?i)\bpkill\s+-f\b"),
            ("recursive-permission-change", r"(?i)\b(?:chmod|chown)\s+-R\b"),
            ("mfa-ssh", r"(?i)\bssh\s+\S+"),
        ]
        return [label for label, pattern in checks if re.search(pattern, value)]


    def create_yoagent_action_preview(self, intent: dict[str, Any]) -> tuple[dict[str, Any], HTTPStatus]:
        locale = normalize_locale(intent.get("locale"))
        session = str(intent.get("session") or "").strip()
        text = truncate_text(str(intent.get("text") or "").strip(), YOAGENT_ACTION_TEXT_LIMIT)
        if not session:
            diagnostic = "missing target session"
            return user_message_payload("yoagent.error.targetSessionRequired", diagnostic), HTTPStatus.BAD_REQUEST
        if not text:
            diagnostic = "missing prompt text"
            return {"session": session, **user_message_payload("yoagent.error.promptTextRequired", diagnostic)}, HTTPStatus.BAD_REQUEST
        target, status = self.deps.yoagent_action_target(session)
        if status != HTTPStatus.OK:
            return target, status
        accepting, acceptance_text = self.deps.yoagent_action_acceptance({**target, "locale": locale})
        prompt_answer = self.yoagent_prompt_answer_plan(text, target, locale) if not accepting and self.yoagent_target_prompt_visible(target) else {}
        prompt_answer_ready = bool(prompt_answer.get("ready"))
        if prompt_answer and not prompt_answer_ready:
            acceptance_text = str(prompt_answer.get("error") or acceptance_text)
        preview_id = f"ya_{secrets.token_urlsafe(12)}"
        risk_labels = self.deps.yoagent_action_risk_labels(text)
        requires_confirmation = bool(intent.get("requires_confirmation") or risk_labels)
        if accepting:
            status_fields = message_fields("status_text", "yoagent.action.status.sendReady", "ready to send now")
        elif prompt_answer_ready:
            status_fields = message_fields("status_text", "yoagent.action.status.answerReady", "ready to answer prompt")
        else:
            status_fields = message_fields("status_text", "yoagent.action.status.waiting", acceptance_text, {"reason": acceptance_text})
        preview = {
            "id": preview_id,
            "type": str(intent.get("type") or "send_prompt"),
            "session": session,
            "text": text,
            "submit": intent.get("submit") is not False,
            "requires_confirmation": requires_confirmation,
            "return_result": bool(intent.get("return_result")),
            "locale": locale,
            "risk_labels": risk_labels,
            "status": "ready" if accepting or prompt_answer_ready else "waiting",
            **status_fields,
            "target": {key: target.get(key) for key in [
                "session",
                "pane_target",
                "pane_window",
                "pane_index",
                "cwd",
                "agent_kind",
                "agent_session_id",
                "agent_model",
                "agent_transcript",
                "transport",
                "transport_label",
                "transport_kind",
                "transport_capabilities",
            ]},
            "screen": target.get("screen") or {},
            "accepting_prompt": accepting,
            "acceptance_text": acceptance_text,
            "created_ts": time.time(),
            "expires_in_seconds": YOAGENT_ACTION_PREVIEW_TTL_SECONDS,
        }
        if prompt_answer:
            preview["prompt_answer"] = {key: prompt_answer.get(key) for key in ["ready", "option", "selected_option", "key", "text", "error"] if key in prompt_answer}
        if isinstance(intent.get("handoff"), dict):
            handoff = intent["handoff"]
            preview["handoff"] = {
                "source_session": str(handoff.get("source_session") or session),
                "session": str(handoff.get("session") or ""),
                "instruction": truncate_text(str(handoff.get("instruction") or ""), YOAGENT_ACTION_TEXT_LIMIT),
            }
        if accepting or prompt_answer_ready:
            with self.yoagent_action_lock:
                self.yoagent_action_previews[preview_id] = copy.deepcopy(preview)
        self.log_event(
            session,
            "yoagent_action_preview",
            f"YO!agent previewed send to {session}",
            {
                "preview_id": preview_id,
                "type": preview["type"],
                "transport": preview["target"].get("transport"),
                "status": preview["status"],
                "risk_labels": risk_labels,
                "text_preview": redacted_action_preview(text),
            },
            message_key="yoagent.action.preview",
        )
        return self.deps.public_yoagent_action_preview(preview), HTTPStatus.OK


    def yoagent_action_answer(self, action: dict[str, Any], locale: str = "") -> str:
        locale = normalize_locale(locale or action.get("locale"))
        target = action.get("target") if isinstance(action.get("target"), dict) else {}
        session = str(action.get("session") or target.get("session") or "")
        transport = normalize_yoagent_transport_id(str(target.get("transport") or ""))
        transport_label = str(target.get("transport_label") or self.yoagent_transports.get(transport).label)
        cwd = str(target.get("cwd") or "").strip()
        action_text = str(action.get("text") or "")
        if action.get("status") == "ready":
            where = yoagent_text(locale, "yoagent.action.location", cwd=cwd) if cwd else ""
            screen = action.get("screen") if isinstance(action.get("screen"), dict) else {}
            clear_note = yoagent_text(locale, "yoagent.action.preparedClear") if str(screen.get("key") or "") == "input-draft" else ""
            return "\n".join([
                yoagent_text(locale, "yoagent.action.prepared", session=session, where=where, clear_note=clear_note),
                "",
                yoagent_text(locale, "yoagent.action.transportAndText", transport=transport_label),
                "",
                f"```text\n{action_text}\n```",
            ])
        screen = action.get("screen") if isinstance(action.get("screen"), dict) else {}
        if str(screen.get("key") or "") == "input-draft":
            detected = str(screen.get("detected_text_preview") or screen.get("detected_text") or "").strip()
            if detected:
                return yoagent_text(locale, "yoagent.action.didNotSendDraft", session=session, text=detected)
        reason = action.get("acceptance_text") or yoagent_text(locale, "yoagent.action.targetNotAccepting")
        return yoagent_text(locale, "yoagent.action.didNotSend", session=session, reason=reason)


    def yoagent_action_preview_details(self, action: dict[str, Any], locale: str = "") -> str:
        locale = normalize_locale(locale or action.get("locale"))
        lines: list[str] = []
        session = str(action.get("session") or "")
        screen = action.get("screen") if isinstance(action.get("screen"), dict) else {}
        screen_key = str(screen.get("key") or "").strip()
        if session:
            lines.append(f"- {yoagent_text(locale, 'common.sessionLabel')}: `{session}`")
        if screen_key:
            lines.append(f"- {yoagent_text(locale, 'yoagent.action.detail.screenState')}: `{screen_key}`")
        if screen_key == "input-draft":
            detected = str(screen.get("detected_text_preview") or screen.get("detected_text") or "").strip()
            if detected:
                lines.append(f"- {yoagent_text(locale, 'yoagent.action.detail.composerText')}: `{detected}`")
        acceptance = str(action.get("acceptance_text") or "").strip()
        if acceptance:
            lines.append(f"- {yoagent_text(locale, 'yoagent.action.detail.sendBlocker')}: {acceptance}")
        return "\n".join(lines)


    def yoagent_action_sent_answer(self, preview: dict[str, Any], result: dict[str, Any], locale: str = "") -> str:
        locale = normalize_locale(locale or preview.get("locale"))
        target = preview.get("target") if isinstance(preview.get("target"), dict) else {}
        session = str(result.get("session") or preview.get("session") or target.get("session") or "")
        agent = " ".join(str(item) for item in [target.get("agent_kind"), target.get("agent_model")] if item)
        agent_text = f" ({agent})" if agent else ""
        transport_label = str(result.get("transport_label") or target.get("transport_label") or self.yoagent_transports.get(str(target.get("transport") or "")).label)
        prompt_text = redacted_action_text(str(preview.get("text") or ""), YOAGENT_ACTION_TEXT_LIMIT)
        lines = [yoagent_text(locale, "yoagent.action.sentVerified", session=session, agent=agent_text)]
        if result.get("cleared_input"):
            lines.append(yoagent_text(locale, "yoagent.action.clearedInput"))
        lines.extend([
            yoagent_text(locale, "yoagent.action.exactPrompt", transport=transport_label),
            "",
            f"```text\n{prompt_text}\n```",
        ])
        if preview.get("return_result") or result.get("return_result"):
            lines.append(yoagent_text(locale, "yoagent.action.awaitingResult"))
        return "\n".join(lines)


    def yoagent_job_answer(self, job: dict[str, Any], locale: str = "") -> str:
        locale = normalize_locale(locale or job.get("locale"))
        job_type = str(job.get("type") or "")
        target = job.get("target") if isinstance(job.get("target"), dict) else {}
        action = job.get("action") if isinstance(job.get("action"), dict) else {}
        status = str(job.get("status") or "")
        if status == "pending_confirmation":
            return yoagent_text(locale, "yoagent.job.answer.pendingConfirmation", id=job.get("id"))
        if job_type == "notify_all_idle":
            roster = ", ".join(f"`{item}`" for item in target.get("roster", []))
            return yoagent_text(locale, "yoagent.job.answer.allIdle", id=job.get("id"), roster=roster)
        if job_type == "notify_session_needs_input":
            return yoagent_text(locale, "yoagent.job.answer.needsInput", id=job.get("id"), session=target.get("session"))
        if job_type == "notify_session_blocked":
            return yoagent_text(locale, "yoagent.job.answer.blocked", id=job.get("id"), session=target.get("session"))
        if job_type == "notify_session_done_after_working":
            return yoagent_text(locale, "yoagent.job.answer.doneAfterWorking", id=job.get("id"), session=target.get("session"))
        if job_type == "wait_then_send":
            return yoagent_text(locale, "yoagent.job.answer.waitThenSend", id=job.get("id"), session=target.get("session"), text=action.get("text") or "")
        return yoagent_text(locale, "yoagent.job.answer.idle", id=job.get("id"), session=target.get("session"))


    def yoagent_action_result_marker(self, target: dict[str, Any]) -> dict[str, Any]:
        transcript = str(target.get("agent_transcript") or "").strip()
        marker: dict[str, Any] = {
            "transcript": transcript,
            "size": 0,
            "mtime_ns": 0,
            "started_ts": time.time(),
            "edited_files": self.yoagent_action_edited_files_snapshot(target),
        }
        if not transcript:
            return marker
        try:
            stat = Path(transcript).expanduser().stat()
        except OSError:
            return marker
        marker["size"] = int(stat.st_size)
        marker["mtime_ns"] = int(stat.st_mtime_ns)
        return marker


    def yoagent_action_edited_files_snapshot(self, target: dict[str, Any]) -> dict[str, str]:
        transcript = str(target.get("agent_transcript") or "").strip()
        agent_kind = str(target.get("agent_kind") or "").strip().lower()
        cwd = str(target.get("cwd") or "").strip() or None
        if not transcript or agent_kind not in YOAGENT_ACTION_AGENT_KINDS:
            return {}
        transcript_path = Path(transcript).expanduser()
        if agent_kind == "claude":
            changes = scan_claude_transcript(transcript_path, cwd)
        elif agent_kind == "codex":
            changes = scan_codex_transcript(transcript_path, cwd)
        else:
            changes = {}
        return {path_text: classify_change(markers) for path_text, markers in sorted(changes.items()) if path_text}


    def yoagent_edited_file_delta_text(self, marker: dict[str, Any], target: dict[str, Any], locale: str = "en") -> str:
        before = marker.get("edited_files") if isinstance(marker.get("edited_files"), dict) else {}
        current = self.yoagent_action_edited_files_snapshot(target)
        rows = [(path_text, status) for path_text, status in current.items() if before.get(path_text) != status]
        if not rows:
            return ""
        lines = [f"{status} {path_text}" for path_text, status in rows[:20]]
        extra = len(rows) - len(lines)
        if extra > 0:
            lines.append(yoagent_text(locale, "yoagent.result.moreFiles", count=extra))
        return yoagent_text(locale, "yoagent.result.editedFiles") + "\n" + "\n".join(lines)


    def yoagent_transcript_delta_text(self, marker: dict[str, Any]) -> str:
        transcript = str(marker.get("transcript") or "").strip()
        if not transcript:
            return ""
        try:
            path = Path(transcript).expanduser()
            start_size = max(0, int(marker.get("size") or 0))
            stat = path.stat()
            with path.open("rb") as handle:
                if stat.st_size >= start_size:
                    handle.seek(start_size)
                data = handle.read(256_000)
        except (OSError, ValueError):
            return ""
        return data.decode("utf-8", errors="replace")


    def yoagent_action_result_text_from_transcript_delta(self, text: str) -> str:
        if not text.strip():
            return ""
        delta_fragments: list[str] = []
        for raw_line in text.splitlines():
            try:
                raw = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            if not isinstance(raw, dict):
                continue
            piece = codex_event_text(raw)
            if piece:
                delta_fragments.append(piece)
            payload = raw.get("payload")
            if isinstance(payload, dict):
                piece = codex_event_text(payload)
                if piece:
                    delta_fragments.append(piece)
        items = compact_transcript_items(text, 120)
        for role in ("assistant", "task_complete"):
            for item in reversed(items):
                if item.get("role") != role:
                    continue
                value = str(item.get("text") or "").strip()
                if value:
                    return truncate_text(value, YOAGENT_ACTION_RESULT_MAX_CHARS)
        joined = "".join(delta_fragments).strip()
        return truncate_text(joined, YOAGENT_ACTION_RESULT_MAX_CHARS) if joined else ""


    def yoagent_action_visible_result_text(self, target: dict[str, Any]) -> str:
        pane_target = str(target.get("pane_target") or target.get("session") or "").strip()
        if not pane_target:
            return ""
        try:
            visible_text = self.deps.tmux_capture_pane(pane_target, visible_only=True)
        except (OSError, subprocess.SubprocessError):
            return ""
        composer_source = self.deps.yoagent_visible_composer_source(pane_target, str(visible_text or ""))
        if self.deps.yoagent_visible_composer_text(composer_source):
            return ""
        lines = [line.rstrip() for line in str(visible_text or "").splitlines() if line.strip()]
        return truncate_text("\n".join(lines[-40:]), YOAGENT_ACTION_RESULT_MAX_CHARS)


    def record_yoagent_action_result(
        self,
        preview: dict[str, Any],
        text: str,
        *,
        timed_out: bool = False,
        partial: bool = False,
    ) -> dict[str, Any] | None:
        target = preview.get("target") if isinstance(preview.get("target"), dict) else {}
        locale = normalize_locale(preview.get("locale"))
        session = str(preview.get("session") or target.get("session") or "")
        transport = normalize_yoagent_transport_id(str(target.get("transport") or ""))
        transport_label = str(target.get("transport_label") or self.yoagent_transports.get(transport).label)
        source_label = yoagent_text(locale, "common.tmuxSession", label=f"`{session}`") if transport == TMUX_LEGACY_TRANSPORT_ID else yoagent_text(locale, "yoagent.result.sourceAgent", transport=transport_label, session=session)
        if timed_out and not text:
            content = yoagent_text(locale, "yoagent.result.timedOut", source=source_label)
        else:
            heading = yoagent_text(locale, "yoagent.result.partialHeading" if partial else "common.result")
            content = yoagent_text(locale, "yoagent.result.withHeading", heading=heading, source=source_label, text=text.strip())
        message = self.record_yoagent_message(
            "assistant",
            content,
            created_at=datetime.now(timezone.utc).isoformat(),
            kind="agent_result",
            session=session,
        )
        self.publish_yoagent_conversation_changed("yoagent_result")
        self.log_event(
            session,
            "yoagent_action_result",
            f"YO!agent recorded result from {session}",
            {
                "timed_out": timed_out,
                "partial": partial,
                "chars": len(text or ""),
                "text_preview": redacted_action_preview(text or ""),
            },
            message_key="events.message.yoagent.actionResult",
            message_params={"session": session},
        )
        return message


    def yoagent_handoff_time_add_prompt(self, instruction: str, text: str) -> str:
        add_match = re.search(r"\badd\s+(\d{1,4})\s*(minutes?|mins?|hours?|hrs?)\b", instruction, flags=re.IGNORECASE)
        if not add_match or not re.search(r"\b(?:if|whether)\s+that\s+is\s+(?:the\s+)?(?:correct|right)(?:\s+time)?(?:\s+now)?\b", instruction, flags=re.IGNORECASE):
            return ""
        time_match = re.search(
            r"\b(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}(?:\s+\w{3,9})?\s+)?"
            r"(?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?\s*(?P<ampm>[ap]\.?\s*m\.?)?\b",
            text,
            flags=re.IGNORECASE,
        )
        if not time_match:
            return ""
        hour = int(time_match.group("hour"))
        minute = int(time_match.group("minute"))
        second = int(time_match.group("second") or 0)
        ampm = re.sub(r"[^apm]", "", str(time_match.group("ampm") or "").lower())
        if ampm == "pm" and hour < 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0
        if hour > 23 or minute > 59 or second > 59:
            return ""
        amount = int(add_match.group(1))
        unit = add_match.group(2).lower()
        delta = timedelta(hours=amount) if unit.startswith(("h", "hr")) else timedelta(minutes=amount)
        adjusted = datetime(2000, 1, 1, hour, minute, second) + delta
        display_hour = adjusted.hour % 12 or 12
        suffix = "AM" if adjusted.hour < 12 else "PM"
        return f"Is {display_hour}:{adjusted.minute:02d} {suffix} the correct time now?"


    def yoagent_derived_handoff_prompt(self, instruction: str, text: str) -> str:
        return self.deps.yoagent_handoff_time_add_prompt(instruction, text)


    def yoagent_source_neutral_handoff_instruction(self, instruction: str) -> str:
        clean = str(instruction or "").strip(" ,.")
        clean = re.sub(r"\bthat\s+result\b", "the context", clean, flags=re.IGNORECASE)
        clean = re.sub(r"\bthat\s+(?:response|answer|reply)\b", "the context", clean, flags=re.IGNORECASE)
        return clean.strip(" ,.") or "answer the user's follow-up using the context"


    def yoagent_handoff_prompt(self, preview: dict[str, Any], text: str) -> str:
        handoff = preview.get("handoff") if isinstance(preview.get("handoff"), dict) else {}
        target_session = str(handoff.get("session") or "").strip()
        instruction = str(handoff.get("instruction") or "").strip()
        if target_session:
            target = re.escape(target_session)
            instruction = re.sub(rf"\band\s+ask\s+(?:tmux\s+)?session\s+[`'\"]?{target}[`'\"]?\s+if\b", "and say if", instruction, flags=re.IGNORECASE)
            instruction = re.sub(rf"\bask\s+(?:tmux\s+)?session\s+[`'\"]?{target}[`'\"]?\s+", "", instruction, flags=re.IGNORECASE)
        instruction = instruction.strip(" ,.")
        if not instruction:
            instruction = "answer the user's follow-up using that reply"
        derived = self.deps.yoagent_derived_handoff_prompt(instruction, text)
        if derived:
            return truncate_text(derived, YOAGENT_ACTION_TEXT_LIMIT)
        instruction = self.deps.yoagent_source_neutral_handoff_instruction(instruction)
        context = " ".join(text.strip().split())
        return truncate_text(
            f"Use this context: {context} Task: {instruction}.",
            YOAGENT_ACTION_TEXT_LIMIT,
        )


    def continue_yoagent_handoff(self, preview: dict[str, Any], text: str) -> dict[str, Any] | None:
        handoff = preview.get("handoff") if isinstance(preview.get("handoff"), dict) else {}
        next_session = str(handoff.get("session") or "").strip()
        if not next_session or not text.strip():
            return None
        intent = {
            "type": "send_prompt",
            "session": next_session,
            "text": self.deps.yoagent_handoff_prompt(preview, text),
            "submit": True,
            "return_result": True,
            "locale": normalize_locale(preview.get("locale")),
        }
        action, action_status = self.deps.create_yoagent_action_preview(intent)
        if action_status == HTTPStatus.OK and action.get("status") == "ready":
            result, result_status = self.deps.execute_yoagent_send_action({"preview_id": action.get("id")}, persist_result=True, start_result_watch=True)
            self.publish_yoagent_conversation_changed("yoagent_handoff")
            return {"ok": result_status == HTTPStatus.OK, "action": action, "result": result, "status": int(result_status)}
        locale = normalize_locale(preview.get("locale"))
        reason = action.get("acceptance_text") or yoagent_user_message_text(locale, action, "yoagent.chat.handoffTargetNotAccepting")
        self.record_yoagent_message(
            "assistant",
            yoagent_text(locale, "yoagent.chat.handoffBlocked", source=preview.get("session"), target=next_session, reason=reason),
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self.publish_yoagent_conversation_changed("yoagent_handoff_blocked")
        return {"ok": False, "action": action, "status": int(action_status), "error": reason}


    def finish_yoagent_action_result(self, preview: dict[str, Any], text: str) -> None:
        self.deps.record_yoagent_action_result(preview, text)
        self.deps.continue_yoagent_handoff(preview, text)


    def run_yoagent_action_result_watcher(
        self,
        preview: dict[str, Any],
        marker: dict[str, Any],
        *,
        watch_id: str | None = None,
        wait_seconds: float = YOAGENT_ACTION_RESULT_WAIT_SECONDS,
        poll_seconds: float = YOAGENT_ACTION_RESULT_POLL_SECONDS,
    ) -> dict[str, Any]:
        try:
            target = preview.get("target") if isinstance(preview.get("target"), dict) else {}
            session = str(preview.get("session") or target.get("session") or "")
            pane_target = str(target.get("pane_target") or session)
            started = time.monotonic()
            deadline = started + max(1.0, float(wait_seconds))
            poll = max(0.1, float(poll_seconds))
            saw_work = False
            last_text = ""
            last_edited_text = ""
            last_change = started
            pause = threading.Event()
            while time.monotonic() < deadline:
                now = time.monotonic()
                delta_text = self.deps.yoagent_transcript_delta_text(marker)
                delta_state = transcript_delta_result_state(delta_text)
                text = self.deps.yoagent_action_result_text_from_transcript_delta(delta_text)
                if text != last_text:
                    last_text = text
                    last_change = now
                edited_text = self.yoagent_edited_file_delta_text(marker, target, normalize_locale(preview.get("locale")))
                if edited_text != last_edited_text:
                    last_edited_text = edited_text
                    last_change = now
                _prompt, screen = self.deps.yoagent_action_pane_status(session, pane_target)
                screen_key = str(screen.get("key") or "idle")
                if screen_key == "working":
                    saw_work = True
                if last_text and delta_state.get("complete") is True:
                    self.deps.finish_yoagent_action_result(preview, last_text)
                    return {"ok": True, "session": session, "source": "transcript", "timed_out": False}
                if (
                    last_text
                    and delta_state.get("has_lifecycle") is not True
                    and delta_state.get("working") is not True
                    and screen_key in YOAGENT_ACTION_ACCEPTING_SCREEN_KEYS
                ):
                    self.deps.finish_yoagent_action_result(preview, last_text)
                    return {"ok": True, "session": session, "source": "transcript", "timed_out": False}
                if (
                    last_text
                    and saw_work
                    and now - last_change >= 2.0
                    and delta_state.get("has_lifecycle") is not True
                    and delta_state.get("working") is not True
                ):
                    self.deps.finish_yoagent_action_result(preview, last_text)
                    return {"ok": True, "session": session, "source": "transcript", "timed_out": False}
                if last_edited_text and screen_key in YOAGENT_ACTION_ACCEPTING_SCREEN_KEYS:
                    self.deps.finish_yoagent_action_result(preview, last_edited_text)
                    return {"ok": True, "session": session, "source": "edited-files", "timed_out": False}
                pause.wait(min(poll, max(0.0, deadline - time.monotonic())))
            if last_text:
                self.deps.record_yoagent_action_result(preview, last_text, timed_out=True, partial=True)
                return {"ok": True, "session": session, "source": "transcript", "timed_out": True, "partial": True}
            if last_edited_text:
                self.deps.record_yoagent_action_result(preview, last_edited_text, timed_out=True, partial=True)
                return {"ok": True, "session": session, "source": "edited-files", "timed_out": True, "partial": True}
            visible_text = self.deps.yoagent_action_visible_result_text(target)
            self.deps.record_yoagent_action_result(preview, visible_text, timed_out=True, partial=bool(visible_text))
            return {"ok": bool(visible_text), "session": session, "source": "screen" if visible_text else "", "timed_out": True}
        finally:
            self.deps.finish_yoagent_action_wait(watch_id, "yoagent_wait_finished")


    def start_yoagent_action_result_watcher(self, preview: dict[str, Any], marker: dict[str, Any]) -> dict[str, Any]:
        watch_id = f"yar_{secrets.token_urlsafe(10)}"
        self.deps.register_yoagent_action_wait(watch_id, preview, marker)
        worker = threading.Thread(
            target=self.run_yoagent_action_result_watcher,
            kwargs={"preview": copy.deepcopy(preview), "marker": copy.deepcopy(marker), "watch_id": watch_id},
            name=f"yoagent-result-{watch_id}",
            daemon=True,
        )
        worker.start()
        return {"id": watch_id, "started": True, "wait_seconds": YOAGENT_ACTION_RESULT_WAIT_SECONDS}


    def yoagent_composer_text_is_idle_placeholder(self, text: str, *, prompt_suggestion: bool = False) -> bool:
        return composer_text_is_idle_placeholder(text, prompt_suggestion=prompt_suggestion)


    def yoagent_visible_composer_source(self, pane_target: str, visible_text: str = "") -> str:
        return visible_composer_source(pane_target, visible_text, capture_styled_func=self.deps.tmux_capture_pane_styled)


    def yoagent_visible_composer_text(self, visible_text: str) -> str:
        return visible_composer_text(visible_text)


    def yoagent_text_still_in_composer(self, target: dict[str, Any], text: str, wait_seconds: float = 0.8, poll_seconds: float = 0.1) -> bool:
        return text_still_in_composer(
            target,
            text,
            wait_seconds=wait_seconds,
            poll_seconds=poll_seconds,
            capture_func=self.deps.tmux_capture_pane,
            capture_styled_func=self.deps.tmux_capture_pane_styled,
        )


    def yoagent_clear_target_composer(
        self,
        target: dict[str, Any],
        *,
        wait_seconds: float = 0.8,
        poll_seconds: float = 0.1,
    ) -> dict[str, Any]:
        return clear_composer(
            target,
            wait_seconds=wait_seconds,
            poll_seconds=poll_seconds,
            capture_func=self.deps.tmux_capture_pane,
            capture_styled_func=self.deps.tmux_capture_pane_styled,
            clear_func=self.deps.tmux_clear_input,
        ).as_dict()


    def preview_yoagent_send_action(self, payload: dict[str, Any]) -> tuple[dict[str, Any], HTTPStatus]:
        intent = {
            "type": str(payload.get("type") or "send_prompt"),
            "session": str(payload.get("session") or payload.get("target") or ""),
            "text": str(payload.get("text") or payload.get("prompt") or ""),
            "submit": payload.get("submit") is not False,
            "requires_confirmation": payload.get("requires_confirmation") is not False,
            "return_result": bool(payload.get("return_result")),
        }
        return self.deps.create_yoagent_action_preview(intent)


    def execute_yoagent_send_action(
        self,
        payload: dict[str, Any],
        persist_result: bool = True,
        start_result_watch: bool = True,
    ) -> tuple[dict[str, Any], HTTPStatus]:
        self.deps.prune_yoagent_action_previews()
        preview_id = str(payload.get("preview_id") or payload.get("id") or "").strip()
        if not preview_id:
            diagnostic = "missing preview_id"
            return user_message_payload("yoagent.error.previewIdRequired", diagnostic), HTTPStatus.BAD_REQUEST
        with self.yoagent_action_lock:
            preview = copy.deepcopy(self.yoagent_action_previews.get(preview_id) or {})
        if not preview:
            diagnostic = "action preview expired or unknown"
            return {"preview_id": preview_id, **user_message_payload("yoagent.error.previewExpired", diagnostic)}, HTTPStatus.NOT_FOUND
        if preview.get("status") != "ready":
            diagnostic = "action is not ready to send"
            return {"preview_id": preview_id, **user_message_payload("yoagent.error.actionNotReady", diagnostic)}, HTTPStatus.CONFLICT
        current, status = self.deps.yoagent_action_target(str(preview.get("session") or ""))
        if status != HTTPStatus.OK:
            return current, status
        target = preview.get("target") if isinstance(preview.get("target"), dict) else {}
        stale_keys = ["pane_target", "agent_kind", "agent_session_id"]
        if any(str(current.get(key) or "") != str(target.get(key) or "") for key in stale_keys):
            diagnostic = "action target changed; create a fresh preview"
            return {
                "preview_id": preview_id,
                "session": preview.get("session"),
                "reason_code": "stale-target",
                **user_message_payload("yoagent.error.actionTargetChanged", diagnostic),
            }, HTTPStatus.CONFLICT
        if normalize_yoagent_transport_id(str(current.get("transport") or "")) != normalize_yoagent_transport_id(str(target.get("transport") or "")):
            diagnostic = "action target changed; create a fresh preview"
            return {
                "preview_id": preview_id,
                "session": preview.get("session"),
                "reason_code": "stale-target",
                **user_message_payload("yoagent.error.actionTargetChanged", diagnostic),
            }, HTTPStatus.CONFLICT
        if isinstance(preview.get("prompt_answer"), dict):
            result, result_status = self.execute_yoagent_prompt_answer(preview, current)
            if result_status == HTTPStatus.OK:
                with self.yoagent_action_lock:
                    self.yoagent_action_previews.pop(preview_id, None)
                self.log_event(
                    str(preview.get("session") or ""),
                    "yoagent_prompt_answered",
                    f"YO!agent answered prompt in {preview.get('session')}",
                    {
                        "preview_id": preview_id,
                        "option": result.get("option"),
                        "key": result.get("key"),
                    },
                    message_key="yoagent.action.answer.selected",
                    message_params={"session": str(preview.get("session") or ""), "option": result.get("option")},
                )
                if persist_result:
                    self.record_yoagent_message("assistant", str(result.get("answer") or ""), created_at=datetime.now(timezone.utc).isoformat())
                    result["conversation"] = self.yoagent_conversation_payload()
            return result, result_status
        accepting, acceptance_text = self.deps.yoagent_action_acceptance({**current, "locale": normalize_locale(preview.get("locale"))})
        if not accepting:
            return {
                "preview_id": preview_id,
                "session": preview.get("session"),
                **user_message_payload("yoagent.error.actionNotReady", acceptance_text),
            }, HTTPStatus.CONFLICT
        target = {
            **target,
            "agent_transcript": current.get("agent_transcript") or target.get("agent_transcript") or "",
            "screen": current.get("screen") if isinstance(current.get("screen"), dict) else target.get("screen") or {},
        }
        target["transport"] = normalize_yoagent_transport_id(str(target.get("transport") or current.get("transport") or ""))
        target["transport_label"] = current.get("transport_label") or target.get("transport_label") or self.yoagent_transports.get(str(target.get("transport") or "")).label
        target["transport_kind"] = current.get("transport_kind") or target.get("transport_kind") or self.yoagent_transports.get(str(target.get("transport") or "")).kind
        preview["target"] = target
        text = str(preview.get("text") or "")
        transport = normalize_yoagent_transport_id(str(target.get("transport") or ""))
        transport_provider = self.yoagent_transports.get(transport)
        return_result = bool(preview.get("return_result") or payload.get("return_result"))
        screen = current.get("screen") if isinstance(current.get("screen"), dict) else {}
        result_marker = self.deps.yoagent_action_result_marker(target) if return_result else {}
        clear_existing = transport == TMUX_LEGACY_TRANSPORT_ID and str(screen.get("key") or "") == "input-draft"
        verify_submit = transport == TMUX_LEGACY_TRANSPORT_ID and preview.get("submit") is not False
        send_result = transport_provider.send(
            target,
            text,
            submit=preview.get("submit") is not False,
            tmux_paste_text=self.deps.tmux_paste_text,
            tmux_capture_pane=self.deps.tmux_capture_pane,
            tmux_capture_pane_styled=self.deps.tmux_capture_pane_styled,
            tmux_clear_input=self.deps.tmux_clear_input,
            clear_existing=clear_existing,
            verify_submit=verify_submit,
        ).as_dict()
        if not send_result.get("ok"):
            clear_result = send_result.get("clear") if isinstance(send_result.get("clear"), dict) else {}
            reason_code = str(send_result.get("reason_code") or "")
            if reason_code == "draft-unclearable":
                self.log_event(
                    str(preview.get("session") or ""),
                    "yoagent_action_clear_failed",
                    f"YO!agent could not clear input before send to {preview.get('session')}",
                    {
                        "preview_id": preview_id,
                        "transport": transport,
                        "cleared_text_preview": redacted_action_preview(str(clear_result.get("detected_text") or "")),
                        "remaining_text_preview": redacted_action_preview(str(clear_result.get("remaining_text") or "")),
                    },
                    message_key="events.message.yoagent.actionClearFailed",
                    message_params={"session": str(preview.get("session") or "")},
                )
            elif reason_code == "unsubmitted":
                with self.yoagent_action_lock:
                    self.yoagent_action_previews.pop(preview_id, None)
                self.log_event(
                    str(preview.get("session") or ""),
                    "yoagent_action_unsubmitted",
                    f"YO!agent send to {preview.get('session')} did not submit",
                    {
                        "preview_id": preview_id,
                        "transport": transport,
                        "text_preview": redacted_action_preview(text),
                    },
                    message_key="events.message.yoagent.actionUnsubmitted",
                    message_params={"session": str(preview.get("session") or "")},
                )
            conflict_reasons = {"approval", "busy", "disconnected", "draft-clearable", "draft-unclearable", "error", "needs-input", "not-agent", "unsubmitted"}
            status_code = HTTPStatus.CONFLICT if reason_code in conflict_reasons else HTTPStatus.INTERNAL_SERVER_ERROR
            response = {
                "preview_id": preview_id,
                "session": preview.get("session"),
                "transport": transport,
                "transport_label": send_result.get("transport_label") or transport_provider.label,
                "sent": False,
                **user_message_payload("yoagent.error.transportSendFailed", send_result.get("error") or "transport send failed"),
            }
            if send_result.get("pasted"):
                response["pasted"] = True
            if reason_code:
                response["reason_code"] = reason_code
            if clear_result:
                response["cleared_input"] = bool(clear_result.get("cleared"))
                response["cleared_text_preview"] = redacted_action_preview(str(clear_result.get("detected_text") or ""))
            return response, status_code
        clear_result = send_result.get("clear") if isinstance(send_result.get("clear"), dict) else {}
        with self.yoagent_action_lock:
            self.yoagent_action_previews.pop(preview_id, None)
        self.log_event(
            str(preview.get("session") or ""),
            "yoagent_action_executed",
            f"YO!agent sent action to {preview.get('session')}",
            {
                "preview_id": preview_id,
                "transport": transport,
                "text_preview": redacted_action_preview(text),
                "cleared_input": bool(send_result.get("cleared")),
                "cleared_text_preview": redacted_action_preview(str(clear_result.get("detected_text") or "")),
            },
            message_key="events.message.yoagent.actionExecuted",
            message_params={"session": str(preview.get("session") or "")},
        )
        response = {
            "ok": True,
            "preview_id": preview_id,
            "session": preview.get("session"),
            "transport": transport,
            "transport_label": send_result.get("transport_label") or transport_provider.label,
            "result_source": send_result.get("result_source") or "",
            "answer": "",
            "sent": True,
            "return_result": return_result,
            "result_marker": result_marker,
        }
        if send_result.get("cleared"):
            response["cleared_input"] = True
            response["cleared_text_preview"] = redacted_action_preview(str(clear_result.get("detected_text") or ""))
        response["answer"] = self.deps.yoagent_action_sent_answer(preview, response)
        if persist_result:
            self.record_yoagent_message(
                "assistant",
                response["answer"],
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            response["conversation"] = self.yoagent_conversation_payload()
        if return_result and send_result.get("text"):
            self.deps.record_yoagent_action_result(preview, str(send_result.get("text") or ""))
            response["result_recorded"] = True
        elif return_result and start_result_watch:
            response["result_watch"] = self.deps.start_yoagent_action_result_watcher(preview, result_marker)
        return response, HTTPStatus.OK


    def reset_yoagent_chat(self) -> dict[str, Any]:
        with self.yoagent_cli_lock:
            self.yoagent_cli_sessions.clear()
            yoagent_conversation.clear_cli_sessions()
        self.deps.close_yoagent_codex_app_server()
        with self.yoagent_action_lock:
            self.yoagent_action_waits.clear()
        yoagent_conversation.clear_messages()
        with self.yoagent_prewarm_lock:
            self.yoagent_prewarm_status = {}
            self.yoagent_startup_response_running = False
        return {"ok": True, "conversation": self.yoagent_conversation_payload()}


    def start_yoagent_backend_prewarm(self, payload: dict[str, Any] | None = None, *, reason: str = "prewarm") -> tuple[dict[str, Any], HTTPStatus]:
        locale = normalize_locale((payload or {}).get("locale"))
        settings = self.yoagent_settings()
        requested_backend = str(settings.get("backend") or "deterministic").strip().lower()
        backend = self.deps.resolve_yoagent_backend(requested_backend)
        invocation = str(settings.get("invocation") or "cli").strip().lower()
        if backend != "codex" or invocation != "cli":
            return {"ok": True, "started": False, "backend": requested_backend, "backend_used": backend, "reason": "no persistent Codex backend available"}, HTTPStatus.OK
        with self.yoagent_prewarm_lock:
            if self.yoagent_prewarm_running:
                return {"ok": True, "started": False, "backend": requested_backend, "backend_used": backend, "reason": "already running"}, HTTPStatus.ACCEPTED
            self.yoagent_prewarm_running = True
            self.yoagent_prewarm_status = {"backend": backend, "reason": reason, "started_at": time.time()}

        def worker() -> None:
            status: dict[str, Any] = {"backend": backend, "reason": reason}
            try:
                thread_id, raw_fallback_reason, cli_status = self.deps.ensure_yoagent_codex_app_server(settings)
                cli_status = dict(cli_status)
                if raw_fallback_reason:
                    cli_status.setdefault("error", raw_fallback_reason)
                fallback_reason = yoagent_cli_fallback_reason(backend, raw_fallback_reason, locale)
                status = {"backend": backend, "reason": reason, "warmed": bool(thread_id and not fallback_reason), "fallback_reason": fallback_reason, "cli": cli_status}
            except Exception as exc:
                status = {"backend": backend, "reason": reason, "warmed": False, "error": str(exc)}
            finally:
                with self.yoagent_prewarm_lock:
                    self.yoagent_prewarm_status = status
                    self.yoagent_prewarm_running = False

        threading.Thread(target=worker, name="yoagent-prewarm", daemon=True).start()
        return {"ok": True, "started": True, "backend": requested_backend, "backend_used": backend}, HTTPStatus.ACCEPTED


    def yoagent_startup_response(self, payload: dict[str, Any] | None = None) -> tuple[dict[str, Any], HTTPStatus]:
        self.deps.maybe_start_yoagent_summary_worker()
        if yoagent_conversation.load_messages(limit=1):
            warm_payload, _warm_status = self.deps.start_yoagent_backend_prewarm(payload, reason="startup_existing_conversation")
            return {
                "ok": True,
                "started": False,
                "visible": False,
                "reason": "conversation already has messages",
                "prewarm": warm_payload,
                "conversation": self.yoagent_conversation_payload(),
            }, HTTPStatus.OK
        settings = self.yoagent_settings()
        requested_backend = str(settings.get("backend") or "deterministic").strip().lower()
        backend = self.deps.resolve_yoagent_backend(requested_backend)
        invocation = str(settings.get("invocation") or "cli").strip().lower()
        locale = str((payload or {}).get("locale") or "en").strip() or "en"
        if backend not in {"codex", "claude"} or invocation != "cli":
            return {
                "ok": True,
                "started": False,
                "visible": False,
                "backend": requested_backend,
                "backend_used": backend,
                "reason": "no CLI backend available",
                "conversation": self.yoagent_conversation_payload(),
            }, HTTPStatus.OK
        with self.yoagent_prewarm_lock:
            if self.yoagent_startup_response_running:
                return {
                    "ok": True,
                    "started": False,
                    "visible": True,
                    "backend": requested_backend,
                    "backend_used": backend,
                    "reason": "startup response already running",
                    "conversation": self.yoagent_conversation_payload(),
                }, HTTPStatus.ACCEPTED
            self.yoagent_startup_response_running = True
        stream_id = f"startup-{uuid.uuid4().hex}"
        started = time.monotonic()
        self.publish_yoagent_stream_delta(stream_id, "", backend=backend, phase="started")
        try:
            activity_payload = self.deps.yoagent_activity_payload()
            answer, fallback_reason, cli_status = self.deps.run_yoagent_cli_backend(
                backend,
                YOAGENT_STARTUP_QUESTION,
                activity_payload,
                settings,
                [],
                locale,
                stream_id=stream_id,
            )
            model_answered = bool(answer)
            backend_used = backend if model_answered else "deterministic"
            if not answer:
                answer = deterministic_yoagent_reply(YOAGENT_STARTUP_QUESTION, activity_payload, settings, locale)
            response: dict[str, Any] = {
                "ok": True,
                "started": True,
                "visible": True,
                "answer": answer,
                "backend": requested_backend,
                "backend_used": backend_used,
                "fallback": bool(fallback_reason),
                **yoagent_fallback_reason_fields(fallback_reason, cli_status),
                "cli": cli_status,
                "timing": {"ttfr_ms": round((time.monotonic() - started) * 1000, 3)},
                "stream_id": stream_id,
                "generated_at": activity_payload.get("generated_at"),
                "session_order": activity_payload.get("session_order", []),
            }
            answer_text, hidden_thinking_removed = strip_yoagent_hidden_thinking(str(response.get("answer") or ""))
            response["answer"] = answer_text
            if hidden_thinking_removed:
                response["hidden_thinking_removed"] = True
            response["detail_rows"] = yoagent_response_detail_rows(response)
            if answer_text:
                stream_fields = self.deps.yoagent_stream_auxiliary_message_fields(stream_id)
                self.record_yoagent_message(
                    "assistant",
                    answer_text,
                    detail_rows=response["detail_rows"],
                    response_ms=yoagent_response_ms(response),
                    **stream_fields,
                )
                self.publish_yoagent_conversation_changed("yoagent_startup")
            response["conversation"] = self.yoagent_conversation_payload()
            if not model_answered:
                self.publish_yoagent_stream_delta(
                    stream_id,
                    answer_text,
                    backend=backend_used,
                    phase="done",
                    done=True,
                    hidden_thinking_removed=bool(response.get("hidden_thinking_removed")),
                )
            return response, HTTPStatus.OK
        except Exception as exc:
            self.publish_yoagent_stream_delta(stream_id, "", backend=backend, phase="error", done=True)
            return {
                "ok": False,
                **user_message_payload("yoagent.error.startupFailed", str(exc)),
                "stream_id": stream_id,
                "conversation": self.yoagent_conversation_payload(),
            }, HTTPStatus.INTERNAL_SERVER_ERROR
        finally:
            with self.yoagent_prewarm_lock:
                self.yoagent_startup_response_running = False


    def yoagent_prewarm(self, payload: dict[str, Any] | None = None) -> tuple[dict[str, Any], HTTPStatus]:
        if (payload or {}).get("visible"):
            return self.deps.yoagent_startup_response(payload)
        return self.deps.start_yoagent_backend_prewarm(payload, reason="client_prewarm")


    def create_yoagent_chat_context(self, payload: dict[str, Any], access_role: str = "admin") -> tuple[YoagentChatContext | None, tuple[dict[str, Any], HTTPStatus] | None]:
        chat_started = time.monotonic()
        locale = normalize_locale(payload.get("locale"))
        question = truncate_text(" ".join(str(payload.get("message") or payload.get("question") or "").split()), 4000)
        if not question:
            return None, (user_message_payload("yoagent.error.chatMessageRequired", "missing YO!agent message"), HTTPStatus.BAD_REQUEST)
        request_id = self.normalize_yoagent_chat_request_id(payload.get("request_id")) or f"chat-{uuid.uuid4().hex}"
        stream_id = self.normalize_yoagent_chat_request_id(payload.get("stream_id")) or request_id
        self.register_yoagent_chat_request(request_id, stream_id)
        history = self.deps.yoagent_prompt_history(payload.get("history", []), question)
        self.record_yoagent_message("user", question)
        settings = self.yoagent_settings()
        force_activity_context = yoagent_question_requests_session_list(question)
        return YoagentChatContext(
            controller=self,
            payload=payload,
            access_role=access_role,
            started=chat_started,
            question=question,
            request_id=request_id,
            stream_id=stream_id,
            history=history,
            settings=settings,
            locale=locale,
            force_activity_context=force_activity_context,
        ), None

    def yoagent_chat_skill_file_response(self, ctx: YoagentChatContext) -> tuple[dict[str, Any], HTTPStatus] | None:
        skill_file_intent = parse_yoagent_skill_file_intent(ctx.question)
        if skill_file_intent:
            if ctx.access_role != "admin":
                return ctx.finish(ctx.base_response(yoagent_text(ctx.locale, "yoagent.chat.skillAdminRequired")))
            return ctx.finish(ctx.base_response(self.yoagent_skill_file_answer(skill_file_intent, ctx.locale)))
        return None

    def yoagent_chat_job_response(self, ctx: YoagentChatContext) -> tuple[dict[str, Any], HTTPStatus] | None:
        job_intent = parse_yoagent_job_intent(ctx.question, self.sessions)
        if job_intent:
            if ctx.access_role != "admin":
                return ctx.finish(ctx.base_response(yoagent_text(ctx.locale, "yoagent.chat.jobAdminRequired")))
            if job_intent.get("type") == "cancel_session_jobs":
                cancel_payload, cancel_status = self.deps.cancel_yoagent_jobs_for_session(str(job_intent.get("session") or ""))
                if cancel_status != HTTPStatus.OK:
                    self.deps.complete_yoagent_chat_request(ctx.request_id)
                    return cancel_payload, cancel_status
                return ctx.finish(ctx.base_response(yoagent_text(ctx.locale, "yoagent.chat.cancelledJobs", count=cancel_payload.get("count", 0), session=cancel_payload.get("session"))))
            job_payload, job_status = self.deps.create_yoagent_job({**job_intent, "locale": ctx.locale})
            if job_status not in {HTTPStatus.OK, HTTPStatus.CONFLICT}:
                self.deps.complete_yoagent_chat_request(ctx.request_id)
                return job_payload, job_status
            job = job_payload.get("job") if isinstance(job_payload.get("job"), dict) else {}
            duplicate = yoagent_text(ctx.locale, "yoagent.chat.reusedJob") if job_payload.get("duplicate") else ""
            return ctx.finish(ctx.base_response(self.deps.yoagent_job_answer(job, ctx.locale) + duplicate))
        return None

    def yoagent_chat_action_response(self, ctx: YoagentChatContext) -> tuple[dict[str, Any], HTTPStatus] | None:
        action_intent = parse_yoagent_action_intent(ctx.question, ctx.history, self.sessions)
        if action_intent:
            if ctx.access_role != "admin":
                return ctx.finish(ctx.base_response(yoagent_text(ctx.locale, "yoagent.chat.actionAdminRequired")))
            action, action_status = self.deps.create_yoagent_action_preview({**action_intent, "locale": ctx.locale})
            if action_status != HTTPStatus.OK:
                self.deps.complete_yoagent_chat_request(ctx.request_id)
                return action, action_status
            confirmation_required = bool(action_intent.get("requires_confirmation") or action.get("requires_confirmation"))
            if action.get("status") == "ready" and not confirmation_required:
                result, result_status = self.deps.execute_yoagent_send_action({"preview_id": action.get("id")}, persist_result=False, start_result_watch=False)
                if result_status == HTTPStatus.OK:
                    if result.get("prompt_answer"):
                        return ctx.finish(ctx.base_response(str(result.get("answer") or self.deps.yoagent_action_answer(action, ctx.locale))))
                    if action.get("return_result") and not result.get("result_recorded"):
                        result["result_watch"] = self.deps.start_yoagent_action_result_watcher(
                            action,
                            result.get("result_marker") if isinstance(result.get("result_marker"), dict) else {},
                        )
                    return ctx.finish(ctx.base_response(self.deps.yoagent_action_sent_answer(action, result, ctx.locale)))
                reason = yoagent_user_message_text(ctx.locale, result, "yoagent.error.actionNotReady")
                return ctx.finish(ctx.base_response(yoagent_text(ctx.locale, "yoagent.chat.didNotSend", reason=reason)))
            if action_intent.get("type") == "wait_then_send" and not confirmation_required:
                job_payload, job_status = self.deps.create_yoagent_job({
                    "type": "wait_then_send",
                    "session": action_intent.get("session"),
                    "text": action_intent.get("text"),
                    "return_result": bool(action_intent.get("return_result")),
                    "locale": ctx.locale,
                })
                if job_status in {HTTPStatus.OK, HTTPStatus.CONFLICT}:
                    job = job_payload.get("job") if isinstance(job_payload.get("job"), dict) else {}
                    duplicate = yoagent_text(ctx.locale, "yoagent.chat.reusedJob") if job_payload.get("duplicate") else ""
                    return ctx.finish(ctx.base_response(self.deps.yoagent_job_answer(job, ctx.locale) + duplicate))
            if not confirmation_required:
                return ctx.finish(ctx.base_response(self.deps.yoagent_action_answer(action, ctx.locale), details=self.deps.yoagent_action_preview_details(action, ctx.locale)))
            return ctx.finish(ctx.base_response(self.deps.yoagent_action_answer(action, ctx.locale), actions=[action], details=self.deps.yoagent_action_preview_details(action, ctx.locale)))
        return None

    def yoagent_chat_work_next_response(self, ctx: YoagentChatContext) -> tuple[dict[str, Any], HTTPStatus] | None:
        if yoagent_question_requests_work_next(ctx.question):
            activity_payload = ctx.get_activity_payload()
            answer = deterministic_yoagent_reply(ctx.question, activity_payload, ctx.settings, ctx.locale)
            return ctx.finish(ctx.base_response(answer, include_activity=True))
        return None

    def yoagent_chat_operator_response(self, ctx: YoagentChatContext) -> tuple[dict[str, Any], HTTPStatus] | None:
        settings_payload_data = self.settings_payload()
        activity_for_operator = ctx.get_activity_payload() if product_state_needs_activity(ctx.question) else {}
        if parse_settings_write(ctx.question, settings_payload_data, ctx.locale) or parse_settings_read(ctx.question, settings_payload_data, ctx.locale):
            activity_for_operator = {}
        operator_response = yoagent_operator_response(
            ctx.question,
            settings_payload_data,
            activity_for_operator,
            ctx.access_role,
            self.save_settings,
            ctx.locale,
        )
        if operator_response:
            operator_response.setdefault("context_lines", yoagent_context_lines(activity_for_operator) if activity_for_operator else [])
            operator_response.setdefault("generated_at", activity_for_operator.get("generated_at") if activity_for_operator else None)
            operator_response.setdefault("session_order", activity_for_operator.get("session_order", []) if activity_for_operator else [])
            return ctx.finish(operator_response)
        return None

    def yoagent_chat_backend_info(self, ctx: YoagentChatContext) -> tuple[str, str, str, dict[str, Any]]:
        requested_backend = str(ctx.settings.get("backend") or "deterministic").strip().lower()
        backend = self.deps.resolve_yoagent_backend(requested_backend)
        invocation = str(ctx.settings.get("invocation") or "cli").strip().lower()
        tool_capabilities = yoagent_chat_tool_capabilities(backend, invocation, ctx.locale)
        return requested_backend, backend, invocation, tool_capabilities

    def yoagent_chat_external_tools_response(self, ctx: YoagentChatContext) -> tuple[dict[str, Any], HTTPStatus] | None:
        _requested_backend, _backend, _invocation, tool_capabilities = self.yoagent_chat_backend_info(ctx)
        external_tool_request = yoagent_question_requests_external_tools(ctx.question)
        if external_tool_request and not tool_capabilities.get("enabled"):
            external_reply = yoagent_live_external_data_reply(tool_capabilities, ctx.locale)
            details = yoagent_text(ctx.locale, "yoagent.tools.policyDetail", reason=tool_capabilities.get("reason") or yoagent_text(ctx.locale, "yoagent.tools.unavailableReason"))
            return ctx.finish(ctx.base_response(external_reply, details=details))
        return None

    def yoagent_chat_cli_or_fallback_response(self, ctx: YoagentChatContext) -> tuple[dict[str, Any], HTTPStatus]:
        requested_backend, backend, invocation, tool_capabilities = self.yoagent_chat_backend_info(ctx)
        external_tool_request = yoagent_question_requests_external_tools(ctx.question)
        answer = ""
        backend_used = "deterministic"
        fallback_reason = ""
        cli_status: dict[str, Any] = {}
        include_model_activity = yoagent_question_needs_activity_context(ctx.question)
        activity_payload = ctx.get_activity_payload() if include_model_activity else {}
        if backend in {"codex", "claude"} and invocation == "cli":
            with self.yoagent_cli_lock:
                if self.deps.yoagent_chat_request_cancelled(ctx.request_id):
                    return ctx.finish_cancelled()
                answer, fallback_reason, cli_status = self.deps.run_yoagent_cli_backend(
                    backend,
                    ctx.question,
                    activity_payload,
                    ctx.settings,
                    ctx.history,
                    ctx.locale,
                    stream_id=ctx.stream_id,
                    request_id=ctx.request_id,
                    include_activity_context=include_model_activity,
                    require_external_tools=external_tool_request,
                )
            if self.deps.yoagent_chat_request_cancelled(ctx.request_id):
                return ctx.finish_cancelled()
            if answer:
                backend_used = backend
        elif backend in {"codex", "claude"} and invocation != "cli":
            fallback_reason = yoagent_text(ctx.locale, "yoagent.tools.invocationUnavailable", backend=backend, invocation=invocation)
            cli_status["fallback_reason_message"] = message_descriptor(
                "yoagent.tools.invocationUnavailable",
                fallback_reason,
                {"backend": backend, "invocation": invocation},
            )
        if not answer:
            answer = deterministic_yoagent_reply(ctx.question, activity_payload if include_model_activity else ctx.get_activity_payload(), ctx.settings, ctx.locale)
        if tool_capabilities:
            cli_status.setdefault("tool_capabilities", tool_capabilities)
        response = ctx.base_response(answer, backend=requested_backend, backend_used=backend_used, fallback_reason=fallback_reason, cli=cli_status, include_activity=include_model_activity)
        if cli_status:
            response["stream_id"] = ctx.stream_id
        return ctx.finish(response)

    def yoagent_chat(self, payload: dict[str, Any], access_role: str = "admin") -> tuple[dict[str, Any], HTTPStatus]:
        ctx, error = self.create_yoagent_chat_context(payload, access_role)
        if error is not None:
            return error
        assert ctx is not None
        for handler in (
            self.yoagent_chat_skill_file_response,
            self.yoagent_chat_job_response,
            self.yoagent_chat_action_response,
            self.yoagent_chat_work_next_response,
            self.yoagent_chat_operator_response,
            self.yoagent_chat_external_tools_response,
        ):
            response = handler(ctx)
            if response is not None:
                return response
        return self.yoagent_chat_cli_or_fallback_response(ctx)
