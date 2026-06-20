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
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from http import HTTPStatus
from pathlib import Path
from typing import Any

from ..approvals import blank_prompt_state
from ..approvals import hybrid_approval_prompt_state
from ..activity_summary import deterministic_yoagent_reply
from ..activity_summary import yoagent_context_lines
from ..activity_summary import yoagent_question_requests_work_next
from ..common import SessionInfo
from ..common import truncate_text
from ..prompt_detector import agent_screen_state
from ..tmux_utils import cmd_error
from ..tmux_utils import tmux_session_target
from ..transcripts import codex_event_text
from ..transcripts import compact_transcript_items
from ..transcripts import session_transcript_activity_state
from ..transcripts import transcript_delta_result_state
from . import conversation as yoagent_conversation
from .actions import parse_yoagent_action_intent
from .actions import parse_yoagent_job_intent
from .actions import parse_yoagent_skill_file_intent
from .actions import redacted_action_preview
from .actions import redacted_action_text
from .backends import YOAGENT_STARTUP_QUESTION
from .backends import strip_yoagent_hidden_thinking
from .backends import yoagent_response_details
from .preferences import parse_settings_read
from .preferences import parse_settings_write
from .preferences import product_state_needs_activity
from .preferences import yoagent_operator_response
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
YOAGENT_ACTION_ACCEPTING_SCREEN_KEYS = {"idle", "done", "needs-input", "input-draft"}
YOAGENT_JOBS_STATE_KEY = "yoagent_jobs"
YOAGENT_JOB_MAX_ITEMS = 200
YOAGENT_JOB_POLL_SECONDS = 1.0
YOAGENT_JOB_DEFAULT_TIMEOUT_MINUTES = 120
YOAGENT_JOB_IDLE_QUIET_SECONDS = 3.0


def normalized_prompt_state(prompt: dict[str, Any] | None = None) -> dict[str, Any]:
    state = blank_prompt_state()
    if prompt:
        state.update(prompt)
    return state


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
        raw_type = str(payload.get("type") or payload.get("kind") or "").strip()
        job_type = raw_type or "notify_session_idle"
        target_payload = payload.get("target") if isinstance(payload.get("target"), dict) else {}
        action_payload = payload.get("action") if isinstance(payload.get("action"), dict) else {}
        session = str(payload.get("session") or target_payload.get("session") or action_payload.get("session") or "").strip()
        if job_type in {"notify_when_idle", "notify_session", "notify_session_idle"}:
            job_type = "notify_session_idle"
            if not session:
                return {"error": "missing target session"}, HTTPStatus.BAD_REQUEST
            unknown = self.require_known_session(session)
            if unknown:
                return unknown
            target = {"session": session}
            predicate = {"type": "session_idle", "quiet_seconds": self.float_value(payload.get("quiet_seconds"), YOAGENT_JOB_IDLE_QUIET_SECONDS)}
            action = {"type": "notify_user", "message": str(payload.get("message") or f"tmux session `{session}` is idle")}
        elif job_type in {"notify_all_idle", "all_idle_summary"}:
            roster = [str(item) for item in (payload.get("roster") if isinstance(payload.get("roster"), list) else self.sessions) if str(item)]
            missing = [item for item in roster if item not in self.sessions]
            if missing:
                return {"error": "unknown sessions in roster", "sessions": missing}, HTTPStatus.NOT_FOUND
            target = {"roster": roster}
            predicate = {"type": "all_idle", "quiet_seconds": self.float_value(payload.get("quiet_seconds"), YOAGENT_JOB_IDLE_QUIET_SECONDS)}
            action = {"type": "notify_user", "message": str(payload.get("message") or "all watched tmux sessions are idle")}
        elif job_type in {"wait_then_send", "wait_then_run"}:
            job_type = "wait_then_send"
            if not session:
                return {"error": "missing target session"}, HTTPStatus.BAD_REQUEST
            unknown = self.require_known_session(session)
            if unknown:
                return unknown
            text = truncate_text(str(payload.get("text") or action_payload.get("text") or "").strip(), YOAGENT_ACTION_TEXT_LIMIT)
            if not text:
                return {"session": session, "error": "missing prompt text"}, HTTPStatus.BAD_REQUEST
            target = {"session": session}
            predicate = {"type": "session_idle", "quiet_seconds": self.float_value(payload.get("quiet_seconds"), YOAGENT_JOB_IDLE_QUIET_SECONDS)}
            return_result = action_payload["return_result"] if "return_result" in action_payload else payload.get("return_result", True)
            action = {"type": "send_prompt", "session": session, "text": text, "submit": action_payload.get("submit") is not False, "return_result": bool(return_result)}
        else:
            return {"error": f"unsupported YO!agent job type: {job_type}"}, HTTPStatus.BAD_REQUEST
        return {"type": job_type, "target": target, "predicate": predicate, "action": action}, HTTPStatus.OK


    def create_yoagent_job(self, payload: dict[str, Any]) -> tuple[dict[str, Any], HTTPStatus]:
        spec, status = self.deps.yoagent_job_spec_from_payload(payload)
        if status != HTTPStatus.OK:
            return spec, status
        job_type = str(spec["type"])
        target = spec["target"]
        predicate = spec["predicate"]
        action = spec["action"]
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
        event = self.log_event(str(target.get("session") or ""), "yoagent_job_created", f"YO!agent job created: {job_type}", {
            "job_id": job["id"],
            "type": job_type,
            "target_session": target.get("session", ""),
            "roster": target.get("roster", []),
            "predicate": predicate.get("type", ""),
            "action": action.get("type", ""),
            "text_preview": redacted_action_preview(str(action.get("text") or "")),
            "risk_labels": risk_labels,
        })
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
                return {"error": "job not found"}, HTTPStatus.NOT_FOUND
            if job.get("status") != "pending_confirmation":
                return {"ok": True, "job": self.deps.public_yoagent_job(job)}, HTTPStatus.OK
            job["status"] = "queued"
            job["confirmed_at"] = datetime.now(timezone.utc).isoformat()
            self.deps.persist_yoagent_jobs_locked()
            public = copy.deepcopy(job)
        self.log_event(str(public.get("target", {}).get("session") or ""), "yoagent_job_confirmed", f"YO!agent job confirmed: {job_id}", {"job_id": job_id})
        self.deps.publish_yoagent_jobs_changed("yoagent_job_confirmed", public)
        self.client_watch_wake_event.set()
        return {"ok": True, "job": self.deps.public_yoagent_job(public)}, HTTPStatus.OK


    def cancel_yoagent_job(self, job_id: str) -> tuple[dict[str, Any], HTTPStatus]:
        with self.yoagent_job_lock:
            job = self.yoagent_jobs.get(str(job_id))
            if not job:
                return {"error": "job not found"}, HTTPStatus.NOT_FOUND
            if job.get("status") in {"fired", "cancelled", "failed", "timed_out"}:
                return {"ok": True, "job": self.deps.public_yoagent_job(job)}, HTTPStatus.OK
            job["status"] = "cancelled"
            job["cancelled_at"] = datetime.now(timezone.utc).isoformat()
            self.deps.persist_yoagent_jobs_locked()
            public = copy.deepcopy(job)
        self.log_event(str(public.get("target", {}).get("session") or ""), "yoagent_job_cancelled", f"YO!agent job cancelled: {job_id}", {"job_id": job_id})
        self.deps.publish_yoagent_jobs_changed("yoagent_job_cancelled", public)
        return {"ok": True, "job": self.deps.public_yoagent_job(public)}, HTTPStatus.OK


    def yoagent_intent(self, payload: dict[str, Any]) -> tuple[dict[str, Any], HTTPStatus]:
        intent = payload.get("intent") if isinstance(payload.get("intent"), dict) else payload
        intent_type = str(intent.get("type") or "")
        if intent_type in {"send_prompt", "wait_then_send", "session_handoff"}:
            preview, status = self.deps.create_yoagent_action_preview(intent)
            risk = "mutating-send" if str(preview.get("status") or "") == "ready" else "waiting-target"
            return {"ok": status == HTTPStatus.OK, "intent": intent, "preview": preview, "risk": risk, "confirmation_required": bool(preview.get("requires_confirmation"))}, status
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
        accepting, acceptance_text = self.deps.yoagent_action_acceptance(current)
        screen = current.get("screen") if isinstance(current.get("screen"), dict) else {}
        state_key = str(screen.get("key") or ("idle" if accepting else "waiting"))
        ready = accepting if predicate_type in {"session_idle", "session_done"} else False
        return {"ready": ready, "state": state_key, "acceptance_text": acceptance_text, "target": current}


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
            }
            self.deps.persist_yoagent_jobs_locked()
            return copy.deepcopy(job), ready and now - since >= quiet_seconds


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
        if action_type == "notify_user":
            message = str(action.get("message") or f"YO!agent job `{job_id}` fired")
            notification = {"title": "YO!agent", "body": message, "session": session}
            completed = self.deps.complete_yoagent_job(job_id, "fired", {"message": message})
            if completed:
                completed["notification"] = notification
                self.log_event(session, "yoagent_job_fired", message, {"job_id": job_id, "type": job.get("type"), "state": observed.get("state", "")})
                self.deps.publish_yoagent_jobs_changed("yoagent_job_fired", completed)
            return
        if action_type == "send_prompt":
            intent = {
                "type": "send_prompt",
                "session": str(action.get("session") or session),
                "text": str(action.get("text") or ""),
                "submit": action.get("submit") is not False,
                "return_result": bool(action.get("return_result")),
            }
            preview, preview_status = self.deps.create_yoagent_action_preview(intent)
            if preview_status == HTTPStatus.OK and preview.get("status") == "ready":
                result, result_status = self.deps.execute_yoagent_send_action({"preview_id": preview.get("id")}, persist_result=True, start_result_watch=bool(intent.get("return_result")))
                completed = self.deps.complete_yoagent_job(job_id, "fired" if result_status == HTTPStatus.OK else "failed", {"action": preview, "send": result, "status": int(result_status)})
                if completed:
                    self.log_event(str(intent.get("session") or ""), "yoagent_job_fired", f"YO!agent job sent prompt to {intent.get('session')}", {"job_id": job_id, "status": int(result_status), "text_preview": redacted_action_preview(str(intent.get("text") or ""))})
                    self.deps.publish_yoagent_jobs_changed("yoagent_job_fired", completed)
                return
            reason = preview.get("acceptance_text") or preview.get("error") or "target is not ready"
            failed = self.deps.complete_yoagent_job(job_id, "failed", {"error": reason, "action": preview})
            if failed:
                self.log_event(str(intent.get("session") or ""), "yoagent_job_failed", f"YO!agent job failed: {reason}", {"job_id": job_id})
                self.deps.publish_yoagent_jobs_changed("yoagent_job_failed", failed)


    def poll_yoagent_jobs_once(self) -> list[str]:
        now = time.time()
        fired: list[str] = []
        with self.yoagent_job_lock:
            jobs = [copy.deepcopy(job) for job in self.yoagent_jobs.values() if job.get("status") == "queued"]
        for job in jobs:
            job_id = str(job.get("id") or "")
            if self.float_value(job.get("timeout_ts"), 0.0) and now >= self.float_value(job.get("timeout_ts"), 0.0):
                timed_out = self.deps.complete_yoagent_job(job_id, "timed_out", {"reason": "timeout"})
                if timed_out:
                    timed_out["notification"] = {
                        "title": "YO!agent",
                        "body": f"YO!agent job `{job_id}` timed out",
                        "session": str((timed_out.get("target") or {}).get("session") or ""),
                    }
                    self.log_event(str((timed_out.get("target") or {}).get("session") or ""), "yoagent_job_timed_out", f"YO!agent job timed out: {job_id}", {"job_id": job_id})
                    self.deps.publish_yoagent_jobs_changed("yoagent_job_timed_out", timed_out)
                continue
            observed = self.deps.yoagent_job_observed_state(job)
            if str(observed.get("state") or "") == "missing":
                reason = str(observed.get("error") or "target session is missing")
                failed = self.deps.complete_yoagent_job(job_id, "failed", {"error": reason, "observed": observed})
                if failed:
                    failed["notification"] = {
                        "title": "YO!agent",
                        "body": f"YO!agent job `{job_id}` failed: {reason}",
                        "session": str((failed.get("target") or {}).get("session") or ""),
                    }
                    self.log_event(str((failed.get("target") or {}).get("session") or ""), "yoagent_job_failed", f"YO!agent job failed: {reason}", {"job_id": job_id})
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
        target = preview.get("target") if isinstance(preview.get("target"), dict) else {}
        session = str(preview.get("session") or target.get("session") or "").strip()
        handoff = preview.get("handoff") if isinstance(preview.get("handoff"), dict) else {}
        target_session = str(handoff.get("session") or "").strip()
        if target_session:
            source_regarding = self.deps.yoagent_wait_regarding_text(preview.get("text"), "the current request")
            target_regarding = self.deps.yoagent_wait_regarding_text(handoff.get("instruction"), "the next request")
            return (
                f"Waiting for tmux session `{session}` to respond (regarding {source_regarding}), before handing off "
                f"the next request to tmux session `{target_session}` (regarding {target_regarding})"
            )
        return f"Waiting for tmux session `{session}` to reply"


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
                "source_regarding": self.deps.yoagent_wait_regarding_text(preview.get("text"), "the current request"),
                "target_regarding": self.deps.yoagent_wait_regarding_text(handoff.get("instruction"), "the next request"),
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


    def yoagent_prompt_history(self, raw_history: Any, question: str) -> list[dict[str, str]]:
        source = raw_history if isinstance(raw_history, list) and raw_history else yoagent_conversation.load_messages()
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


    def yoagent_activity_payload(self) -> dict[str, Any]:
        payload = dict(self.activity_summary_payload(session_scope="all"))
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
            return {"session": session, "error": "session metadata unavailable", "errors": errors}, HTTPStatus.NOT_FOUND
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
        try:
            visible_text = self.deps.tmux_capture_pane(target_pane, visible_only=True)
            if visible_text is None:
                prompt = self.deps.normalized_prompt_state()
                prompt["error"] = "failed to capture pane"
                return prompt, {"key": "disconnected", "text": "failed to capture pane"}
            prompt_state = hybrid_approval_prompt_state(session, visible_text, prompt_source=self.auto_approve_prompt_source())
            if prompt_state.get("visible") and prompt_state.get("type") == "bash":
                pane_text = self.deps.tmux_capture_pane(target_pane)
                prompt_state = hybrid_approval_prompt_state(session, visible_text, pane_text or visible_text, prompt_source=self.auto_approve_prompt_source())
            screen_state = agent_screen_state(visible_text)
            composer_text = self.deps.yoagent_visible_composer_text(visible_text)
            if screen_state.get("key") == "idle" and composer_text:
                screen_state = {
                    "key": "input-draft",
                    "text": "target input box already contains unsent text",
                    "detected_text": composer_text,
                }
            if screen_state.get("key") == "idle":
                infos = discovered_sessions
                if infos is None:
                    infos, _errors = self.deps.discover_sessions([session])
                transcript_state = session_transcript_activity_state(infos.get(session))
                if transcript_state.get("key") != "idle":
                    screen_state = transcript_state
            return self.deps.normalized_prompt_state(prompt_state), dict(screen_state)
        except (OSError, subprocess.SubprocessError) as exc:
            prompt = self.deps.normalized_prompt_state()
            prompt["error"] = str(exc)
            return prompt, {"key": "error", "text": str(exc)}


    def yoagent_action_acceptance(self, target: dict[str, Any]) -> tuple[bool, str]:
        agent_kind = str(target.get("agent_kind") or "").strip().lower()
        if agent_kind not in YOAGENT_ACTION_AGENT_KINDS:
            return False, "target pane does not have a detected Claude or Codex agent"
        if not str(target.get("pane_target") or "").strip():
            return False, "target pane is missing"
        prompt = target.get("prompt") if isinstance(target.get("prompt"), dict) else {}
        if prompt.get("visible"):
            return False, "target agent is at an approval prompt, not a fresh AI prompt"
        screen = target.get("screen") if isinstance(target.get("screen"), dict) else {}
        screen_key = str(screen.get("key") or "idle")
        if screen_key == "input-draft":
            return True, "target input box has unsent text; YO!agent will clear it before sending"
        if screen_key in YOAGENT_ACTION_ACCEPTING_SCREEN_KEYS:
            return True, "target agent is accepting an AI prompt"
        if screen_key == "working":
            return False, "target agent is still working"
        if screen_key in {"approval", "needs-approval", "yolo-approval"}:
            return False, "target agent is at an approval prompt"
        if screen_key in {"disconnected", "error"}:
            return False, str(screen.get("text") or "target pane is not reachable")
        return False, f"target agent is not accepting an AI prompt ({screen_key})"


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
        session = str(intent.get("session") or "").strip()
        text = truncate_text(str(intent.get("text") or "").strip(), YOAGENT_ACTION_TEXT_LIMIT)
        if not session:
            return {"error": "missing target session"}, HTTPStatus.BAD_REQUEST
        if not text:
            return {"session": session, "error": "missing prompt text"}, HTTPStatus.BAD_REQUEST
        target, status = self.deps.yoagent_action_target(session)
        if status != HTTPStatus.OK:
            return target, status
        accepting, acceptance_text = self.deps.yoagent_action_acceptance(target)
        preview_id = f"ya_{secrets.token_urlsafe(12)}"
        risk_labels = self.deps.yoagent_action_risk_labels(text)
        requires_confirmation = bool(intent.get("requires_confirmation") or risk_labels)
        preview = {
            "id": preview_id,
            "type": str(intent.get("type") or "send_prompt"),
            "session": session,
            "text": text,
            "submit": intent.get("submit") is not False,
            "requires_confirmation": requires_confirmation,
            "return_result": bool(intent.get("return_result")),
            "risk_labels": risk_labels,
            "status": "ready" if accepting else "waiting",
            "status_text": "ready to send now" if accepting else acceptance_text,
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
        if isinstance(intent.get("handoff"), dict):
            handoff = intent["handoff"]
            preview["handoff"] = {
                "source_session": str(handoff.get("source_session") or session),
                "session": str(handoff.get("session") or ""),
                "instruction": truncate_text(str(handoff.get("instruction") or ""), YOAGENT_ACTION_TEXT_LIMIT),
            }
        if accepting:
            with self.yoagent_action_lock:
                self.yoagent_action_previews[preview_id] = copy.deepcopy(preview)
        self.log_event(session, "yoagent_action_preview", f"YO!agent previewed send to {session}", {
            "preview_id": preview_id,
            "type": preview["type"],
            "transport": preview["target"].get("transport"),
            "status": preview["status"],
            "risk_labels": risk_labels,
            "text_preview": redacted_action_preview(text),
        })
        return self.deps.public_yoagent_action_preview(preview), HTTPStatus.OK


    def yoagent_action_answer(self, action: dict[str, Any]) -> str:
        target = action.get("target") if isinstance(action.get("target"), dict) else {}
        session = str(action.get("session") or target.get("session") or "")
        transport = normalize_yoagent_transport_id(str(target.get("transport") or ""))
        transport_label = str(target.get("transport_label") or self.yoagent_transports.get(transport).label)
        cwd = str(target.get("cwd") or "").strip()
        action_text = str(action.get("text") or "")
        if action.get("status") == "ready":
            where = f" in `{cwd}`" if cwd else ""
            screen = action.get("screen") if isinstance(action.get("screen"), dict) else {}
            clear_note = " I will clear the existing target input before sending." if str(screen.get("key") or "") == "input-draft" else ""
            return "\n".join([
                f"I resolved tmux session `{session}`{where} and prepared a confirmed send action.{clear_note}",
                "",
                f"Transport: `{transport_label}`. Text to send:",
                "",
                f"```text\n{action_text}\n```",
            ])
        screen = action.get("screen") if isinstance(action.get("screen"), dict) else {}
        if str(screen.get("key") or "") == "input-draft":
            detected = str(screen.get("detected_text_preview") or screen.get("detected_text") or "").strip()
            if detected:
                return f"I resolved tmux session `{session}`, but I did not send anything because the target input box already contains unsent text: `{detected}`."
        return f"I resolved tmux session `{session}`, but I did not send anything because {action.get('acceptance_text') or 'the target is not accepting an AI prompt'}."


    def yoagent_action_preview_details(self, action: dict[str, Any]) -> str:
        lines: list[str] = []
        session = str(action.get("session") or "")
        screen = action.get("screen") if isinstance(action.get("screen"), dict) else {}
        screen_key = str(screen.get("key") or "").strip()
        if session:
            lines.append(f"- target session: `{session}`")
        if screen_key:
            lines.append(f"- target screen state: `{screen_key}`")
        if screen_key == "input-draft":
            detected = str(screen.get("detected_text_preview") or screen.get("detected_text") or "").strip()
            if detected:
                lines.append(f"- detected target composer text: `{detected}`")
        acceptance = str(action.get("acceptance_text") or "").strip()
        if acceptance:
            lines.append(f"- send blocker: {acceptance}")
        return "\n".join(lines)


    def yoagent_action_sent_answer(self, preview: dict[str, Any], result: dict[str, Any]) -> str:
        target = preview.get("target") if isinstance(preview.get("target"), dict) else {}
        session = str(result.get("session") or preview.get("session") or target.get("session") or "")
        agent = " ".join(str(item) for item in [target.get("agent_kind"), target.get("agent_model")] if item)
        agent_text = f" ({agent})" if agent else ""
        transport_label = str(result.get("transport_label") or target.get("transport_label") or self.yoagent_transports.get(str(target.get("transport") or "")).label)
        prompt_text = redacted_action_text(str(preview.get("text") or ""), YOAGENT_ACTION_TEXT_LIMIT)
        lines = [f"I verified tmux session `{session}`{agent_text} is accepting an AI prompt."]
        if result.get("cleared_input"):
            lines.append("I cleared existing target input first.")
        lines.extend([
            f"I am sending this exact prompt through `{transport_label}`:",
            "",
            f"```text\n{prompt_text}\n```",
        ])
        if preview.get("return_result") or result.get("return_result"):
            lines.append("I am awaiting the response and I'll show the result here when it replies.")
        return "\n".join(lines)


    def yoagent_job_answer(self, job: dict[str, Any]) -> str:
        job_type = str(job.get("type") or "")
        target = job.get("target") if isinstance(job.get("target"), dict) else {}
        action = job.get("action") if isinstance(job.get("action"), dict) else {}
        status = str(job.get("status") or "")
        if status == "pending_confirmation":
            return f"I created YO!agent job `{job.get('id')}` and it is waiting for confirmation."
        if job_type == "notify_all_idle":
            roster = ", ".join(f"`{item}`" for item in target.get("roster", []))
            return f"I created YO!agent job `{job.get('id')}` to notify you when all watched tmux sessions are idle: {roster}."
        if job_type == "wait_then_send":
            return f"I created YO!agent job `{job.get('id')}` to wait for tmux session `{target.get('session')}` to accept an AI prompt, then send:\n\n```text\n{action.get('text') or ''}\n```"
        return f"I created YO!agent job `{job.get('id')}` to notify you when tmux session `{target.get('session')}` is idle."


    def yoagent_action_result_marker(self, target: dict[str, Any]) -> dict[str, Any]:
        transcript = str(target.get("agent_transcript") or "").strip()
        marker: dict[str, Any] = {
            "transcript": transcript,
            "size": 0,
            "mtime_ns": 0,
            "started_ts": time.time(),
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
        if self.deps.yoagent_visible_composer_text(str(visible_text or "")):
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
        session = str(preview.get("session") or target.get("session") or "")
        transport = normalize_yoagent_transport_id(str(target.get("transport") or ""))
        transport_label = str(target.get("transport_label") or self.yoagent_transports.get(transport).label)
        source_label = f"tmux session `{session}`" if transport == TMUX_LEGACY_TRANSPORT_ID else f"{transport_label} target `{session}`"
        if timed_out and not text:
            content = f"I sent the request to {source_label}, but I did not see a result before the wait timed out."
        else:
            heading = "Partial result" if partial else "Result"
            content = f"{heading} from {source_label}:\n\n{text.strip()}"
        message = self.record_yoagent_message(
            "assistant",
            content,
            created_at=datetime.now(timezone.utc).isoformat(),
            kind="agent_result",
            session=session,
        )
        self.publish_yoagent_conversation_changed("yoagent_result")
        self.log_event(session, "yoagent_action_result", f"YO!agent recorded result from {session}", {
            "timed_out": timed_out,
            "partial": partial,
            "chars": len(text or ""),
            "text_preview": redacted_action_preview(text or ""),
        })
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
        }
        action, action_status = self.deps.create_yoagent_action_preview(intent)
        if action_status == HTTPStatus.OK and action.get("status") == "ready":
            result, result_status = self.deps.execute_yoagent_send_action({"preview_id": action.get("id")}, persist_result=True, start_result_watch=True)
            self.publish_yoagent_conversation_changed("yoagent_handoff")
            return {"ok": result_status == HTTPStatus.OK, "action": action, "result": result, "status": int(result_status)}
        reason = action.get("acceptance_text") or action.get("error") or "the next target is not accepting an AI prompt"
        self.record_yoagent_message(
            "assistant",
            f"I got the result from tmux session `{preview.get('session')}`, but I did not send the handoff to tmux session `{next_session}` because {reason}.",
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
                pause.wait(min(poll, max(0.0, deadline - time.monotonic())))
            if last_text:
                self.deps.record_yoagent_action_result(preview, last_text, timed_out=True, partial=True)
                return {"ok": True, "session": session, "source": "transcript", "timed_out": True, "partial": True}
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


    def yoagent_composer_text_is_idle_placeholder(self, text: str) -> bool:
        candidate = " ".join(str(text or "").split()).strip()
        if not candidate:
            return False
        if re.fullmatch(r'Try\s+(?:"[^"\n]{1,200}"|“[^“”\n]{1,200}”)', candidate):
            return True
        return re.fullmatch(r"Implement\s+\{[^{}\n]{1,80}\}", candidate) is not None


    def yoagent_visible_composer_text(self, visible_text: str) -> str:
        lines = [line.replace("\xa0", " ") for line in str(visible_text or "").splitlines()[-80:]]
        footer_index = -1
        for index in range(len(lines) - 1, -1, -1):
            stripped = lines[index].strip()
            if stripped.startswith(("▶▶", "⏵⏵", "gpt-", "claude ")):
                footer_index = index
                break
        if footer_index < 0:
            return ""
        end_index = footer_index
        for index in range(footer_index - 1, -1, -1):
            if re.match(r"^[─━╌╍]{3,}$", lines[index].strip()):
                end_index = index
                break
        start_index = max(0, end_index - 8)
        for index in range(end_index - 1, -1, -1):
            if re.match(r"^[─━╌╍]{3,}$", lines[index].strip()):
                start_index = index + 1
                break
        lines = lines[start_index:end_index]
        prompt_index = -1
        first_line = ""
        for index in range(len(lines) - 1, -1, -1):
            line = lines[index]
            match = re.match(r"^\s*[❯›>]\s+(?P<text>\S.*)$", line)
            if not match:
                continue
            if re.match(r"^\s*[❯›>]\s*\d+[.:]\s+\S", line):
                continue
            prompt_index = index
            first_line = match.group("text").strip()
            break
        if prompt_index < 0 or not first_line:
            return ""
        block = [first_line]
        for line in lines[prompt_index + 1:]:
            stripped = line.strip()
            if re.match(r"^[─━╌╍]{3,}$", stripped):
                break
            if stripped.startswith(("▶▶", "⏵⏵", "new task?", "gpt-", "claude ")):
                break
            if stripped.startswith(("●", "✻", "⎿", "⤷")) or re.match(r"^(Ran|Bash|Write|Read|Edit|Update|Search)\b", stripped):
                return ""
            block.append(stripped)
        parts = [part for part in block if part]
        composer_text = " ".join(parts).strip()
        if len(parts) == 1 and self.deps.yoagent_composer_text_is_idle_placeholder(composer_text):
            return ""
        return composer_text


    def yoagent_text_still_in_composer(self, target: dict[str, Any], text: str, wait_seconds: float = 0.8, poll_seconds: float = 0.1) -> bool:
        pane_target = str(target.get("pane_target") or target.get("session") or "").strip()
        needle = " ".join(str(text or "").split())
        if not pane_target or not needle:
            return False
        deadline = time.monotonic() + max(0.0, wait_seconds)
        pause = threading.Event()
        while True:
            try:
                visible_text = self.deps.tmux_capture_pane(pane_target, visible_only=True) or ""
            except (OSError, subprocess.SubprocessError):
                return False
            pending = " ".join(self.deps.yoagent_visible_composer_text(visible_text).split())
            if not pending:
                return False
            prefix_len = min(120, len(needle))
            if needle in pending or pending in needle or needle[:prefix_len] in pending:
                if time.monotonic() >= deadline:
                    return True
                pause.wait(min(max(0.01, poll_seconds), max(0.0, deadline - time.monotonic())))
                continue
            return False


    def yoagent_clear_target_composer(
        self,
        target: dict[str, Any],
        *,
        wait_seconds: float = 0.8,
        poll_seconds: float = 0.1,
    ) -> dict[str, Any]:
        pane_target = str(target.get("pane_target") or target.get("session") or "").strip()
        if not pane_target:
            return {"ok": False, "cleared": False, "error": "target pane is missing"}
        try:
            visible_text = self.deps.tmux_capture_pane(pane_target, visible_only=True) or ""
        except (OSError, subprocess.SubprocessError) as exc:
            return {"ok": False, "cleared": False, "error": str(exc)}
        detected = self.deps.yoagent_visible_composer_text(visible_text)
        if self.deps.yoagent_composer_text_is_idle_placeholder(detected):
            return {"ok": True, "cleared": False, "detected_text": ""}
        if not detected:
            return {"ok": True, "cleared": False, "detected_text": ""}
        result = self.deps.tmux_clear_input(pane_target)
        if result.returncode != 0:
            return {
                "ok": False,
                "cleared": False,
                "detected_text": detected,
                "error": cmd_error(result, "tmux send-keys C-u failed"),
            }
        deadline = time.monotonic() + max(0.0, wait_seconds)
        pause = threading.Event()
        while True:
            try:
                visible_text = self.deps.tmux_capture_pane(pane_target, visible_only=True) or ""
            except (OSError, subprocess.SubprocessError) as exc:
                return {"ok": False, "cleared": False, "detected_text": detected, "error": str(exc)}
            remaining = self.deps.yoagent_visible_composer_text(visible_text)
            if self.deps.yoagent_composer_text_is_idle_placeholder(remaining):
                return {"ok": True, "cleared": True, "detected_text": detected, "remaining_text": remaining, "remaining_placeholder": True}
            if not remaining:
                return {"ok": True, "cleared": True, "detected_text": detected}
            if time.monotonic() >= deadline:
                return {
                    "ok": False,
                    "cleared": False,
                    "detected_text": detected,
                    "remaining_text": remaining,
                    "error": "target input box did not clear",
                }
            pause.wait(min(max(0.01, poll_seconds), max(0.0, deadline - time.monotonic())))


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
            return {"error": "missing preview_id"}, HTTPStatus.BAD_REQUEST
        with self.yoagent_action_lock:
            preview = copy.deepcopy(self.yoagent_action_previews.get(preview_id) or {})
        if not preview:
            return {"preview_id": preview_id, "error": "action preview expired or unknown"}, HTTPStatus.NOT_FOUND
        if preview.get("status") != "ready":
            return {"preview_id": preview_id, "error": "action is not ready to send"}, HTTPStatus.CONFLICT
        current, status = self.deps.yoagent_action_target(str(preview.get("session") or ""))
        if status != HTTPStatus.OK:
            return current, status
        accepting, acceptance_text = self.deps.yoagent_action_acceptance(current)
        if not accepting:
            return {"preview_id": preview_id, "session": preview.get("session"), "error": acceptance_text}, HTTPStatus.CONFLICT
        target = preview.get("target") if isinstance(preview.get("target"), dict) else {}
        stale_keys = ["pane_target", "agent_kind", "agent_session_id"]
        if any(str(current.get(key) or "") != str(target.get(key) or "") for key in stale_keys):
            return {"preview_id": preview_id, "session": preview.get("session"), "error": "action target changed; create a fresh preview"}, HTTPStatus.CONFLICT
        if normalize_yoagent_transport_id(str(current.get("transport") or "")) != normalize_yoagent_transport_id(str(target.get("transport") or "")):
            return {"preview_id": preview_id, "session": preview.get("session"), "error": "action target changed; create a fresh preview"}, HTTPStatus.CONFLICT
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
        clear_result: dict[str, Any] = {}
        screen = current.get("screen") if isinstance(current.get("screen"), dict) else {}
        if transport == TMUX_LEGACY_TRANSPORT_ID and str(screen.get("key") or "") == "input-draft":
            clear_result = self.deps.yoagent_clear_target_composer(target)
            cleared_text_preview = redacted_action_preview(str(clear_result.get("detected_text") or ""))
            if not clear_result.get("ok"):
                self.log_event(str(preview.get("session") or ""), "yoagent_action_clear_failed", f"YO!agent could not clear input before send to {preview.get('session')}", {
                    "preview_id": preview_id,
                    "transport": transport,
                    "cleared_text_preview": cleared_text_preview,
                    "remaining_text_preview": redacted_action_preview(str(clear_result.get("remaining_text") or "")),
                })
                return {
                    "preview_id": preview_id,
                    "session": preview.get("session"),
                    "transport": transport,
                    "transport_label": transport_provider.label,
                    "sent": False,
                    "cleared_input": False,
                    "cleared_text_preview": cleared_text_preview,
                    "error": clear_result.get("error") or "target input box did not clear",
                }, HTTPStatus.CONFLICT
        result_marker = self.deps.yoagent_action_result_marker(target) if return_result else {}
        send_result = transport_provider.send(
            target,
            text,
            submit=preview.get("submit") is not False,
            tmux_paste_text=self.deps.tmux_paste_text,
        ).as_dict()
        if not send_result.get("ok"):
            return {
                "preview_id": preview_id,
                "session": preview.get("session"),
                "transport": transport,
                "transport_label": send_result.get("transport_label") or transport_provider.label,
                "error": send_result.get("error") or "transport send failed",
            }, HTTPStatus.INTERNAL_SERVER_ERROR
        if transport == TMUX_LEGACY_TRANSPORT_ID and preview.get("submit") is not False and self.deps.yoagent_text_still_in_composer(target, text):
            with self.yoagent_action_lock:
                self.yoagent_action_previews.pop(preview_id, None)
            error = "pasted text is still in the target input box after Return; target did not submit it"
            self.log_event(str(preview.get("session") or ""), "yoagent_action_unsubmitted", f"YO!agent send to {preview.get('session')} did not submit", {
                "preview_id": preview_id,
                "transport": transport,
                "text_preview": redacted_action_preview(text),
            })
            return {
                "preview_id": preview_id,
                "session": preview.get("session"),
                "transport": transport,
                "transport_label": transport_provider.label,
                "sent": False,
                "pasted": True,
                "error": error,
            }, HTTPStatus.CONFLICT
        with self.yoagent_action_lock:
            self.yoagent_action_previews.pop(preview_id, None)
        self.log_event(str(preview.get("session") or ""), "yoagent_action_executed", f"YO!agent sent action to {preview.get('session')}", {
            "preview_id": preview_id,
            "transport": transport,
            "text_preview": redacted_action_preview(text),
            "cleared_input": bool(clear_result.get("cleared")),
            "cleared_text_preview": redacted_action_preview(str(clear_result.get("detected_text") or "")),
        })
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
        if clear_result.get("cleared"):
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
                thread_id, fallback_reason, cli_status = self.deps.ensure_yoagent_codex_app_server(settings)
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
                "fallback_reason": fallback_reason,
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
            response["details"] = yoagent_response_details(response)
            if answer_text:
                self.record_yoagent_message("assistant", answer_text, details=str(response.get("details") or ""))
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
            return {"ok": False, "error": str(exc), "stream_id": stream_id, "conversation": self.yoagent_conversation_payload()}, HTTPStatus.INTERNAL_SERVER_ERROR
        finally:
            with self.yoagent_prewarm_lock:
                self.yoagent_startup_response_running = False


    def yoagent_prewarm(self, payload: dict[str, Any] | None = None) -> tuple[dict[str, Any], HTTPStatus]:
        if (payload or {}).get("visible"):
            return self.deps.yoagent_startup_response(payload)
        return self.deps.start_yoagent_backend_prewarm(payload, reason="client_prewarm")


    def yoagent_chat(self, payload: dict[str, Any], access_role: str = "admin") -> tuple[dict[str, Any], HTTPStatus]:
        chat_started = time.monotonic()
        question = truncate_text(" ".join(str(payload.get("message") or payload.get("question") or "").split()), 4000)
        if not question:
            return {"error": "missing YO!agent message"}, HTTPStatus.BAD_REQUEST
        history = self.deps.yoagent_prompt_history(payload.get("history", []), question)
        self.record_yoagent_message("user", question)
        settings = self.yoagent_settings()
        locale = str(payload.get("locale") or "en").strip()
        activity_payload_cache: dict[str, Any] | None = None
        context_lines_cache: list[str] | None = None

        def get_activity_payload() -> dict[str, Any]:
            nonlocal activity_payload_cache
            if activity_payload_cache is None:
                activity_payload_cache = self.deps.yoagent_activity_payload()
            return activity_payload_cache

        def get_context_lines() -> list[str]:
            nonlocal context_lines_cache
            if context_lines_cache is None:
                context_lines_cache = yoagent_context_lines(get_activity_payload())
            return context_lines_cache

        def finish(response: dict[str, Any]) -> tuple[dict[str, Any], HTTPStatus]:
            response.setdefault("answered_at", datetime.now(timezone.utc).isoformat())
            timing = response.get("timing") if isinstance(response.get("timing"), dict) else {}
            timing.setdefault("ttfr_ms", round((time.monotonic() - chat_started) * 1000, 3))
            response["timing"] = timing
            answer_text = str(response.get("answer") or "")
            answer_text, hidden_thinking_removed = strip_yoagent_hidden_thinking(answer_text)
            response["answer"] = answer_text
            if hidden_thinking_removed:
                response["hidden_thinking_removed"] = True
            details = str(response.get("details") or "").strip()
            generated_details = yoagent_response_details(response)
            response["details"] = "\n".join(part for part in [details, generated_details] if part).strip()
            actions = response.get("actions") if isinstance(response.get("actions"), list) else []
            if answer_text:
                self.record_yoagent_message("assistant", answer_text, actions=actions, details=str(response.get("details") or ""))
            response["conversation"] = self.yoagent_conversation_payload()
            return response, HTTPStatus.OK

        def base_response(
            answer: str,
            *,
            actions: list[dict[str, Any]] | None = None,
            backend: str = "yolomux",
            backend_used: str = "yolomux",
            fallback_reason: str = "",
            details: str = "",
            cli: dict[str, Any] | None = None,
            include_activity: bool = False,
        ) -> dict[str, Any]:
            response_activity = get_activity_payload() if include_activity else {}
            return {
                "answer": answer,
                "actions": actions or [],
                "backend": backend,
                "backend_used": backend_used,
                "fallback": bool(fallback_reason),
                "fallback_reason": fallback_reason,
                "details": details,
                "cli": cli or {},
                "context_lines": get_context_lines() if include_activity else [],
                "generated_at": response_activity.get("generated_at"),
                "session_order": response_activity.get("session_order", []),
            }

        skill_file_intent = parse_yoagent_skill_file_intent(question)
        if skill_file_intent:
            if access_role != "admin":
                return finish(base_response("YO!skill and context file management requires an admin login. I did not change anything."))
            return finish(base_response(self.yoagent_skill_file_answer(skill_file_intent)))

        job_intent = parse_yoagent_job_intent(question, self.sessions)
        if job_intent:
            if access_role != "admin":
                return finish(base_response("YO!agent watch and notify jobs require an admin login. I did not create a job."))
            job_payload, job_status = self.deps.create_yoagent_job(job_intent)
            if job_status not in {HTTPStatus.OK, HTTPStatus.CONFLICT}:
                return {"error": job_payload.get("error") or "failed to create YO!agent job", **job_payload}, job_status
            job = job_payload.get("job") if isinstance(job_payload.get("job"), dict) else {}
            duplicate = " I reused the existing matching job." if job_payload.get("duplicate") else ""
            return finish(base_response(self.deps.yoagent_job_answer(job) + duplicate))

        action_intent = parse_yoagent_action_intent(question, history, self.sessions)
        if action_intent:
            if access_role != "admin":
                return finish(base_response("Sending prompts to tmux sessions through YO!agent requires an admin login. I did not send anything."))
            action, action_status = self.deps.create_yoagent_action_preview(action_intent)
            if action_status != HTTPStatus.OK:
                return {"error": action.get("error") or "failed to create YO!agent action", **action}, action_status
            confirmation_required = bool(action_intent.get("requires_confirmation") or action.get("requires_confirmation"))
            if action.get("status") == "ready" and not confirmation_required:
                result, result_status = self.deps.execute_yoagent_send_action({"preview_id": action.get("id")}, persist_result=False, start_result_watch=False)
                if result_status == HTTPStatus.OK:
                    if action.get("return_result") and not result.get("result_recorded"):
                        result["result_watch"] = self.deps.start_yoagent_action_result_watcher(
                            action,
                            result.get("result_marker") if isinstance(result.get("result_marker"), dict) else {},
                        )
                    return finish(base_response(self.deps.yoagent_action_sent_answer(action, result)))
                return finish(base_response(f"I did not send anything because {result.get('error') or 'the target is not accepting an AI prompt'}."))
            if action_intent.get("type") == "wait_then_send" and not confirmation_required:
                job_payload, job_status = self.deps.create_yoagent_job({
                    "type": "wait_then_send",
                    "session": action_intent.get("session"),
                    "text": action_intent.get("text"),
                    "return_result": bool(action_intent.get("return_result")),
                })
                if job_status in {HTTPStatus.OK, HTTPStatus.CONFLICT}:
                    job = job_payload.get("job") if isinstance(job_payload.get("job"), dict) else {}
                    duplicate = " I reused the existing matching job." if job_payload.get("duplicate") else ""
                    return finish(base_response(self.deps.yoagent_job_answer(job) + duplicate))
            if not confirmation_required:
                return finish(base_response(self.deps.yoagent_action_answer(action), details=self.deps.yoagent_action_preview_details(action)))
            return finish(base_response(self.deps.yoagent_action_answer(action), actions=[action], details=self.deps.yoagent_action_preview_details(action)))
        settings_payload_data = self.settings_payload()
        if yoagent_question_requests_work_next(question):
            activity_payload = get_activity_payload()
            answer = deterministic_yoagent_reply(question, activity_payload, settings, locale)
            return finish(base_response(answer, include_activity=True))
        activity_for_operator = get_activity_payload() if product_state_needs_activity(question) else {}
        if parse_settings_write(question, settings_payload_data) or parse_settings_read(question, settings_payload_data):
            activity_for_operator = {}
        operator_response = yoagent_operator_response(
            question,
            settings_payload_data,
            activity_for_operator,
            access_role,
            self.save_settings,
        )
        if operator_response:
            operator_response.setdefault("context_lines", yoagent_context_lines(activity_for_operator) if activity_for_operator else [])
            operator_response.setdefault("generated_at", activity_for_operator.get("generated_at") if activity_for_operator else None)
            operator_response.setdefault("session_order", activity_for_operator.get("session_order", []) if activity_for_operator else [])
            return finish(operator_response)
        requested_backend = str(settings.get("backend") or "deterministic").strip().lower()
        backend = self.deps.resolve_yoagent_backend(requested_backend)
        invocation = str(settings.get("invocation") or "cli").strip().lower()
        answer = ""
        backend_used = "deterministic"
        fallback_reason = ""
        cli_status: dict[str, Any] = {}
        activity_payload = get_activity_payload()
        stream_id = f"chat-{uuid.uuid4().hex}"
        if backend in {"codex", "claude"} and invocation == "cli":
            answer, fallback_reason, cli_status = self.deps.run_yoagent_cli_backend(backend, question, activity_payload, settings, history, locale, stream_id=stream_id)
            if answer:
                backend_used = backend
        elif backend in {"codex", "claude"} and invocation != "cli":
            fallback_reason = f"{backend} {invocation} invocation is not available yet"
        if not answer:
            answer = deterministic_yoagent_reply(question, activity_payload, settings, locale)
        response = base_response(answer, backend=requested_backend, backend_used=backend_used, fallback_reason=fallback_reason, cli=cli_status, include_activity=True)
        if cli_status:
            response["stream_id"] = stream_id
        return finish(response)
