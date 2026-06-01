from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import asdict
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from http import HTTPStatus
from pathlib import Path
from typing import Any

import auto_approve_tmux

from . import session_files
from . import yolo_rules
from .activity_summary import activity_signature
from .activity_summary import build_global_activity_summary
from .activity_summary import build_session_activity_summary
from .activity_summary import build_yoagent_chat_prompt
from .activity_summary import build_yoagent_resume_prompt
from .activity_summary import deterministic_yoagent_reply
from .activity_summary import yoagent_context_lines
from .auto_approve_worker import AutoApproveWorker
from .auto_approve_worker import auto_approve_lock_message
from .auto_approve_worker import auto_approve_lock_owner
from .common import AGENT_COMMANDS
from .common import EVENT_LOG_PATH
from .common import MAX_COMPACT_TRANSCRIPT_ITEMS
from .common import MAX_EVENT_TAIL_LINES
from .common import MAX_TRANSCRIPT_TAIL_LINES
from .common import MAX_YOLOMUX_SESSION_TABS
from .common import PROJECT_ROOT
from .common import SERVER_HOSTNAME
from .common import SUMMARY_MAX_PROMPT_CHARS
from .common import SUMMARY_CODEX_EFFORT
from .common import SUMMARY_CODEX_MODEL
from .common import SUMMARY_CODEX_SERVICE_TIER
from .common import UPLOAD_MAX_FILES
from .common import UPLOAD_MAX_BYTES
from .common import next_numbered_session_name
from .common import tail_file_lines
from .common import truncate_text
from .control import YolomuxControlServer
from .control import send_yolomux_control_request
from .events import EventLog
from .events import read_yolomux_state
from .events import update_yolomux_state
from .metadata import MetadataCache
from .metadata import candidate_session_cwds
from .metadata import focus_root_for_session
from .metadata import github_checks_unknown
from .metadata import project_inventory
from .metadata import pull_request_number_from_subject
from .metadata import session_git_inventory
from .metadata import session_project_metadata
from .metadata import session_to_json
from .sessions import discover_sessions
from .settings import save_settings
from .settings import settings_payload
from .transcripts import codex_summary_prompt
from .transcripts import codex_event_text
from .transcripts import compact_summary_lines
from .transcripts import compact_transcript_items
from .transcripts import compact_transcript_items_since
from .transcripts import compact_transcript_lines
from .transcripts import format_transcript_item
from .transcripts import session_transcript_activity_state
from .transcripts import trim_prompt_text
from .tmux_utils import list_tmux_session_names
from .tmux_utils import tmux
from .tmux_utils import tmux_has_exact_session
from .tmux_utils import tmux_session_target
from .uploads import sanitize_upload_filename
from .uploads import unique_upload_path
from .workdir import agent_command
from .workdir import available_agent_commands
from .workdir import resolved_upload_dir
from .workdir import session_workdir


METADATA_BADGE_PULSE_SECONDS = 20.0
METADATA_BADGES = ("main", "pr", "status", "ci")
METADATA_BADGE_SIGNATURES_STATE_KEY = "metadata_badge_signatures"
METADATA_BADGE_PULSE_UNTIL_STATE_KEY = "metadata_badge_pulse_until"
YOAGENT_CLI_TIMEOUT_SECONDS = 45
YOAGENT_CLI_SESSION_IDLE_SECONDS = 300
YOAGENT_AUTH_FAILURE_RE = re.compile(
    r"(not\s+logged\s+in|log\s*in|login|required\s+auth|authentication|unauthorized|permission\s+denied|401)",
    re.IGNORECASE,
)
YOAGENT_PERMISSION_BLOCK_RE = re.compile(
    r"(?:i'?m\s+blocked|harness\s+denied|grant\s+.*permission|approve\s+the\s+next\s+bash|need\s+read(?:/bash)?\s+permission)",
    re.IGNORECASE,
)
# Keep in sync with tmuxSessionNameError() in static/yolomux.js.
TMUX_SESSION_NAME_RE = re.compile(r"^[A-Za-z0-9_. -]{1,64}$")


def yoagent_cli_auth_failure(text: str) -> bool:
    return bool(YOAGENT_AUTH_FAILURE_RE.search(text or ""))


def yoagent_cli_answer_is_permission_blocked(text: str) -> bool:
    return bool(YOAGENT_PERMISSION_BLOCK_RE.search(text or ""))


def yoagent_cli_fallback_reason(backend: str, error: str) -> str:
    text = truncate_text(" ".join(str(error or "").split()), 600)
    if not text:
        return ""
    if not yoagent_cli_auth_failure(text):
        return text
    label = "Claude CLI" if backend == "claude" else "Codex CLI" if backend == "codex" else f"{backend} CLI"
    login_command = "claude login" if backend == "claude" else "codex login" if backend == "codex" else f"{backend} login"
    return f"{label} is not logged in. Run `{login_command}`; showing the No agent YO!agent summary."


def codex_event_session_id(event: dict[str, Any]) -> str:
    for key in ("session_id", "sessionId", "thread_id", "threadId", "conversation_id", "conversationId"):
        value = event.get(key)
        if isinstance(value, str) and value:
            return value
    for key in ("session", "thread", "conversation"):
        value = event.get(key)
        if isinstance(value, dict):
            nested_id = value.get("id")
            if isinstance(nested_id, str) and nested_id:
                return nested_id
            nested = codex_event_session_id(value)
            if nested:
                return nested
    return ""


def yoagent_activity_payload_signature(activity_payload: dict[str, Any]) -> str:
    try:
        return json.dumps(activity_payload, sort_keys=True, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        return str(activity_payload)


def tmux_session_name_error(name: str) -> str | None:
    if not name:
        return "session name is required"
    if len(name) > 64:
        return "session name must be 64 characters or fewer"
    if not TMUX_SESSION_NAME_RE.fullmatch(name):
        return "session name may contain only letters, numbers, spaces, dot, dash, and underscore"
    return None


class TmuxWebtermApp:
    def __init__(self, sessions: list[str], dangerously_yolo: bool = False):
        self.sessions = sessions
        self.dangerously_yolo = dangerously_yolo
        self.auto_workers: dict[str, AutoApproveWorker] = {}
        self.auto_workers_lock = threading.RLock()
        self.metadata_cache = MetadataCache()
        self.metadata_warm_lock = threading.Lock()
        self.metadata_warm_running = False
        self.metadata_badge_lock = threading.Lock()
        self.metadata_badge_signatures: dict[str, dict[str, str]] = {}
        self.metadata_badge_pulse_until: dict[str, dict[str, float]] = {}
        self.activity_summary_lock = threading.RLock()
        self.activity_summary_cache: dict[str, dict[str, Any]] = {}
        self.yoagent_cli_lock = threading.RLock()
        self.yoagent_cli_sessions: dict[str, dict[str, Any]] = {}
        self.load_metadata_badge_state()
        self.event_log = EventLog(EVENT_LOG_PATH)
        self.control_server = YolomuxControlServer(self.handle_control_request)
        self.control_server.start()

    def refresh_sessions(self) -> list[str]:
        sessions, error = list_tmux_session_names()
        if error is None:
            self.sessions = sessions
            return []
        return [error]

    def persisted_auto_sessions(self) -> list[str]:
        enabled = read_yolomux_state().get("auto_approve_enabled", [])
        if not isinstance(enabled, list):
            return []
        return [session for session in enabled if isinstance(session, str) and session in self.sessions]

    def set_persisted_auto_session(self, session: str, enabled: bool) -> None:
        state = read_yolomux_state()
        current = state.get("auto_approve_enabled", [])
        sessions = {name for name in current if isinstance(name, str)} if isinstance(current, list) else set()
        if enabled:
            sessions.add(session)
        else:
            sessions.discard(session)
        update_yolomux_state({"auto_approve_enabled": sorted(sessions)})

    def persist_auto_sessions(self) -> None:
        with self.auto_workers_lock:
            local_enabled = {name for name, worker in self.auto_workers.items() if worker.alive()}
        current = read_yolomux_state().get("auto_approve_enabled", [])
        if isinstance(current, list):
            external_enabled = {
                session
                for session in current
                if isinstance(session, str) and session not in self.auto_workers and auto_approve_lock_owner(session)
            }
        else:
            external_enabled = set()
        update_yolomux_state({"auto_approve_enabled": sorted(local_enabled | external_enabled)})

    def notify_status(self) -> dict[str, Any]:
        return {"enabled": bool(read_yolomux_state().get("notify_enabled", False))}

    def settings_payload(self) -> dict[str, Any]:
        return settings_payload()

    def activity_summary_payload(self, force: bool = False) -> dict[str, Any]:
        sessions, errors = discover_sessions(self.sessions)
        self.warm_metadata_cache_async(sessions)
        summaries: dict[str, Any] = {}
        ordered_summaries: list[dict[str, Any]] = []
        with self.activity_summary_lock:
            if force:
                self.activity_summary_cache.clear()
            for session in self.sessions:
                info = sessions.get(session)
                if info is None:
                    continue
                project = session_project_metadata(info, self.metadata_cache, allow_network=False)
                files_payload = session_files.session_files_payload_for_info(info, hours=24.0)
                signature = activity_signature(info, project, files_payload)
                cached = self.activity_summary_cache.get(session)
                if cached and cached.get("signature") == signature:
                    summary = cached["summary"]
                else:
                    summary = build_session_activity_summary(info, project, files_payload)
                    self.activity_summary_cache[session] = {"signature": signature, "summary": summary}
                summaries[session] = summary
                ordered_summaries.append(summary)
            for session in list(self.activity_summary_cache):
                if session not in sessions:
                    self.activity_summary_cache.pop(session, None)
        generated = datetime.now(timezone.utc)
        return {
            "generated_at": generated.isoformat(),
            "generated_ts": generated.timestamp(),
            "session_order": [session for session in self.sessions if session in summaries],
            "sessions": summaries,
            "global": build_global_activity_summary(ordered_summaries, errors),
            "errors": errors,
        }

    def yoagent_settings(self) -> dict[str, Any]:
        settings = settings_payload().get("settings", {}).get("yoagent", {})
        return settings if isinstance(settings, dict) else {}

    def reset_yoagent_chat(self) -> dict[str, Any]:
        with self.yoagent_cli_lock:
            self.yoagent_cli_sessions.clear()
        return {"ok": True}

    def yoagent_chat(self, payload: dict[str, Any]) -> tuple[dict[str, Any], HTTPStatus]:
        question = truncate_text(" ".join(str(payload.get("message") or payload.get("question") or "").split()), 4000)
        if not question:
            return {"error": "missing YO!agent message"}, HTTPStatus.BAD_REQUEST
        raw_history = payload.get("history", [])
        history = []
        if isinstance(raw_history, list):
            for item in raw_history[-8:]:
                if not isinstance(item, dict):
                    continue
                role = "user" if item.get("role") == "user" else "assistant"
                content = truncate_text(" ".join(str(item.get("content") or "").split()), 1000)
                if content:
                    history.append({"role": role, "content": content})
        settings = self.yoagent_settings()
        activity_payload = self.activity_summary_payload()
        context_lines = yoagent_context_lines(activity_payload)
        backend = str(settings.get("backend") or "deterministic").strip().lower()
        invocation = str(settings.get("invocation") or "cli").strip().lower()
        answer = ""
        backend_used = "deterministic"
        fallback_reason = ""
        cli_status: dict[str, Any] = {}
        if backend in {"codex", "claude"} and invocation == "cli":
            answer, fallback_reason, cli_status = self.run_yoagent_cli_backend(backend, question, activity_payload, settings, history)
            if answer:
                backend_used = backend
        elif backend in {"codex", "claude"} and invocation != "cli":
            fallback_reason = f"{backend} {invocation} invocation is not available yet"
        if not answer:
            answer = deterministic_yoagent_reply(question, activity_payload, settings)
        return {
            "answer": answer,
            "backend": backend,
            "backend_used": backend_used,
            "fallback": bool(fallback_reason),
            "fallback_reason": fallback_reason,
            "cli": cli_status,
            "context_lines": context_lines,
            "generated_at": activity_payload.get("generated_at"),
            "session_order": activity_payload.get("session_order", []),
        }, HTTPStatus.OK

    def run_yoagent_cli_backend(
        self,
        backend: str,
        question: str,
        activity_payload: dict[str, Any],
        settings: dict[str, Any],
        history: list[dict[str, str]],
    ) -> tuple[str, str, dict[str, Any]]:
        if backend not in {"codex", "claude"}:
            return "", f"unknown backend: {backend}", {}

        with self.yoagent_cli_lock:
            now = time.monotonic()
            state = self.yoagent_cli_sessions.get(backend, {})
            if state and now - float(state.get("updated_monotonic") or 0) > YOAGENT_CLI_SESSION_IDLE_SECONDS:
                state = {}
                self.yoagent_cli_sessions.pop(backend, None)
            session_id = str(state.get("session_id") or "")
            context_signature = yoagent_activity_payload_signature(activity_payload)
            context_changed = context_signature != state.get("activity_signature")
            seed = not session_id
            next_session_id = session_id or (str(uuid.uuid4()) if backend == "claude" else "")
            prompt = build_yoagent_chat_prompt(question, activity_payload, settings, history) if seed else build_yoagent_resume_prompt(question, activity_payload, settings, context_changed)

        started = time.monotonic()
        if backend == "codex":
            answer, error, captured_session_id = self.run_yoagent_codex_cli(prompt, session_id=session_id, resume=not seed)
            next_session_id = captured_session_id or session_id
        else:
            answer, error = self.run_yoagent_claude_cli(prompt, session_id=next_session_id, resume=not seed)
        if answer and yoagent_cli_answer_is_permission_blocked(answer):
            answer = ""
            error = "YO!agent backend tried to inspect files outside the supplied YOLOmux context; showing the No agent summary instead."
        elapsed_ms = round((time.monotonic() - started) * 1000)
        fallback_reason = yoagent_cli_fallback_reason(backend, error)
        status = {
            "backend": backend,
            "resumed": not seed,
            "seeded": seed,
            "context_changed": context_changed,
            "prompt_chars": len(prompt),
            "elapsed_ms": elapsed_ms,
            "session_id": next_session_id or None,
            "per_server": True,
        }
        with self.yoagent_cli_lock:
            if answer and next_session_id:
                self.yoagent_cli_sessions[backend] = {
                    "session_id": next_session_id,
                    "activity_signature": context_signature,
                    "updated_monotonic": time.monotonic(),
                }
            elif fallback_reason:
                self.yoagent_cli_sessions.pop(backend, None)
        return answer, fallback_reason, status

    def run_yoagent_codex_cli(self, prompt: str, session_id: str = "", resume: bool = False) -> tuple[str, str, str]:
        if not shutil.which("codex"):
            return "", "codex CLI not found", ""
        common = [
            "--json",
            "-m",
            SUMMARY_CODEX_MODEL,
            "-c",
            f'model_reasoning_effort="{SUMMARY_CODEX_EFFORT}"',
            "-c",
            f'service_tier="{SUMMARY_CODEX_SERVICE_TIER}"',
            "--ignore-rules",
        ]
        if resume and session_id:
            args = ["codex", "exec", "resume", *common, session_id, "-"]
        else:
            args = ["codex", "exec", *common, "--sandbox", "read-only", "--cd", str(PROJECT_ROOT), "-"]
        try:
            completed = subprocess.run(
                args,
                input=prompt,
                cwd=str(PROJECT_ROOT),
                env={**os.environ, "TERM": "xterm-256color", "NO_COLOR": "1"},
                text=True,
                capture_output=True,
                timeout=YOAGENT_CLI_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return "", str(exc), ""
        text_parts = []
        captured_session_id = ""
        for line in completed.stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            captured_session_id = captured_session_id or codex_event_session_id(event)
            text = codex_event_text(event)
            if text:
                text_parts.append(text)
        if text_parts:
            return "\n".join(text_parts).strip(), "", captured_session_id
        error = completed.stderr.strip() or f"codex exited {completed.returncode}"
        return "", error, captured_session_id

    def run_yoagent_claude_cli(self, prompt: str, session_id: str = "", resume: bool = False) -> tuple[str, str]:
        if not shutil.which("claude"):
            return "", "claude CLI not found"
        args = ["claude", "-p"]
        if resume and session_id:
            args.extend(["--resume", session_id])
        elif session_id:
            args.extend(["--session-id", session_id])
        args.append(prompt)
        try:
            completed = subprocess.run(
                args,
                cwd=str(PROJECT_ROOT),
                env={**os.environ, "TERM": "xterm-256color", "NO_COLOR": "1"},
                text=True,
                capture_output=True,
                timeout=YOAGENT_CLI_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return "", str(exc)
        if completed.returncode == 0 and completed.stdout.strip():
            return completed.stdout.strip(), ""
        return "", completed.stderr.strip() or f"claude exited {completed.returncode}"

    def save_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        return save_settings(patch)

    def yolo_rules_payload(self) -> dict[str, Any]:
        return yolo_rules.rules_status()

    def reload_yolo_rules(self) -> dict[str, Any]:
        return yolo_rules.reload_rules()

    def ensure_yolo_rules_file(self) -> dict[str, Any]:
        yolo_rules.ensure_rule_file()
        return yolo_rules.reload_rules()

    def auto_approve_interval_seconds(self) -> float:
        value = settings_payload().get("settings", {}).get("performance", {}).get("auto_approve_interval_seconds", 0.5)
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.5

    def auto_approve_prompt_source(self) -> str:
        value = settings_payload().get("settings", {}).get("yolo", {}).get("prompt_source", "hybrid")
        return value if value in {"pane", "hybrid"} else "hybrid"

    def metadata_badge_pulse_seconds(self) -> float:
        value = settings_payload().get("settings", {}).get("appearance", {}).get("metadata_badge_pulse_seconds", METADATA_BADGE_PULSE_SECONDS)
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            return METADATA_BADGE_PULSE_SECONDS

    def set_notify(self, enabled: bool) -> dict[str, Any]:
        update_yolomux_state({"notify_enabled": enabled})
        self.log_event(None, "notify_enabled" if enabled else "notify_disabled", "Notify enabled" if enabled else "Notify disabled", {})
        return {"enabled": enabled}

    def log_event(self, session: str | None, event_type: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.event_log.append(session, event_type, message, details)

    def log_auto_event(self, session: str, event_type: str, message: str, details: dict[str, Any]) -> None:
        self.log_event(session, event_type, message, details)

    def events_payload(self, session: str | None = None, limit: int = 100) -> tuple[dict[str, Any], HTTPStatus]:
        if session and session not in self.sessions:
            return {"error": f"unknown session: {session}"}, HTTPStatus.NOT_FOUND
        bounded_limit = max(1, min(limit, MAX_EVENT_TAIL_LINES))
        return {
            "events": self.event_log.tail(session=session, limit=bounded_limit),
            "session": session or "",
            "limit": bounded_limit,
        }, HTTPStatus.OK

    def search_payload(self, query: str, session: str | None = None, limit: int = 100) -> tuple[dict[str, Any], HTTPStatus]:
        if session and session not in self.sessions:
            return {"error": f"unknown session: {session}"}, HTTPStatus.NOT_FOUND
        text = str(query or "").strip()
        bounded_limit = max(1, min(limit, MAX_EVENT_TAIL_LINES))
        event_matches = self.event_log.search(text, session=session, limit=bounded_limit)
        summary_matches: list[dict[str, Any]] = []
        if text:
            search_sessions = [session] if session else self.sessions
            needle = text.lower()
            for name in search_sessions:
                summary, status = self.summary(name)
                summary_text = summary.get("text") if status == HTTPStatus.OK else ""
                if isinstance(summary_text, str) and needle in summary_text.lower():
                    summary_matches.append({
                        "session": name,
                        "type": "summary",
                        "text": truncate_text(summary_text, 2000),
                    })
                    if len(summary_matches) >= bounded_limit:
                        break
        return {
            "query": text,
            "session": session or "",
            "limit": bounded_limit,
            "events": event_matches,
            "summaries": summary_matches,
        }, HTTPStatus.OK

    def run_history_payload(self, session: str | None = None) -> tuple[dict[str, Any], HTTPStatus]:
        refresh_errors = self.refresh_sessions()
        if session and session not in self.sessions:
            return {"error": f"unknown session: {session}"}, HTTPStatus.NOT_FOUND
        scope = [session] if session else self.sessions
        infos, errors = discover_sessions(scope)
        runs: list[dict[str, Any]] = []
        for name in scope:
            info = infos.get(name)
            if info is None:
                continue
            selected = info.selected_pane
            agent = next((item for item in info.agents if item.transcript), info.agents[0] if info.agents else None)
            project = session_project_metadata(info, self.metadata_cache, allow_network=False)
            transcript_mtime = 0.0
            if agent and agent.transcript:
                transcript_mtime = session_files.file_mtime(Path(agent.transcript))
            runs.append({
                "session": name,
                "agent": asdict(agent) if agent else None,
                "cwd": agent.cwd if agent and agent.cwd else selected.current_path if selected else "",
                "tmux_target": selected.target if selected else "",
                "tmux_command": selected.process_label or selected.command if selected else "",
                "transcript_mtime": transcript_mtime,
                "project": project,
                "recent_events": self.event_log.tail(session=name, limit=5),
            })
        runs.sort(key=lambda item: (-float(item.get("transcript_mtime") or 0), item["session"]))
        return {"session": session or "", "runs": runs, "errors": [*refresh_errors, *errors]}, HTTPStatus.OK

    def session_files_payload(
        self,
        session: str | None = None,
        hours: float = 24.0,
        from_ref: str | None = None,
        to_ref: str | None = None,
    ) -> tuple[dict[str, Any], HTTPStatus]:
        refresh_errors = self.refresh_sessions()
        if session and session not in self.sessions:
            return {"error": f"unknown session: {session}", "session": session}, HTTPStatus.NOT_FOUND
        scope = [session] if session else self.sessions
        infos, errors = discover_sessions(scope)
        payload, status = session_files.session_files_payload(session, infos, hours, from_ref=from_ref, to_ref=to_ref)
        payload["errors"] = [*refresh_errors, *errors, *payload.get("errors", [])]
        return payload, status

    def client_event(self, event: dict[str, Any]) -> tuple[dict[str, Any], HTTPStatus]:
        session = event.get("session")
        if session is not None and session not in self.sessions:
            return {"error": f"unknown session: {session}"}, HTTPStatus.NOT_FOUND
        event_type = event.get("type")
        message = event.get("message")
        if not isinstance(event_type, str) or not event_type:
            return {"error": "missing event type"}, HTTPStatus.BAD_REQUEST
        if not isinstance(message, str) or not message:
            return {"error": "missing event message"}, HTTPStatus.BAD_REQUEST
        details = event.get("details")
        if not isinstance(details, dict):
            details = {}
        saved = self.log_event(session, event_type, message, details)
        return {"ok": True, "event": saved}, HTTPStatus.OK

    def restore_auto_approve(self) -> list[str]:
        restored: list[str] = []
        for session in self.persisted_auto_sessions():
            payload, status = self.set_auto_approve(session, True, persist=False, takeover=False)
            if status == HTTPStatus.OK and payload.get("enabled") is True:
                restored.append(session)
        return restored

    def handle_control_request(self, request: dict[str, Any]) -> dict[str, Any]:
        action = request.get("action")
        if action == "disable_auto_approve":
            session = request.get("session")
            requester = request.get("requester")
            return self.disable_auto_approve_for_takeover(session, requester if isinstance(requester, dict) else {})
        return {"ok": False, "error": f"unknown action: {action}"}

    def disable_auto_approve_for_takeover(self, session: Any, requester: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(session, str) or session not in self.sessions:
            return {"ok": False, "error": f"unknown session: {session}"}
        with self.auto_workers_lock:
            worker = self.auto_workers.get(session)
            if worker is None:
                return {"ok": True, "session": session, "enabled": False, "message": "YOLO was not enabled here"}
            worker.stop()
            self.auto_workers.pop(session, None)
        self.log_event(session, "yolo_released", "YOLO released for another server", {"requester": requester})
        return {"ok": True, "session": session, "enabled": False}

    def transcripts_payload(self) -> dict[str, Any]:
        refresh_errors = self.refresh_sessions()
        sessions, errors = discover_sessions(self.sessions)
        session_payloads = {
            name: session_to_json(info, self.metadata_cache, allow_network=False)
            for name, info in sessions.items()
        }
        payload = {
            "server_time": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
            "session_order": self.sessions,
            "sessions": session_payloads,
            "errors": [*refresh_errors, *errors],
        }
        self.apply_metadata_badge_pulses(session_payloads)
        self.warm_metadata_cache_async(sessions)
        return payload

    def apply_metadata_badge_pulses(self, session_payloads: dict[str, dict[str, Any]]) -> None:
        now = time.time()
        next_signatures = {
            session: self.metadata_badge_signatures_for_session(payload)
            for session, payload in session_payloads.items()
        }
        with self.metadata_badge_lock:
            state_changed = False
            for session, next_signature in list(next_signatures.items()):
                previous_signature = self.metadata_badge_signatures.get(session)
                if previous_signature and self.metadata_badge_change_is_cold_cache_degradation(previous_signature, next_signature):
                    next_signatures[session] = previous_signature

            for session, badge_times in list(self.metadata_badge_pulse_until.items()):
                current = {badge: until for badge, until in badge_times.items() if until > now}
                if current != badge_times:
                    state_changed = True
                if current:
                    self.metadata_badge_pulse_until[session] = current
                else:
                    self.metadata_badge_pulse_until.pop(session, None)

            for session, next_signature in next_signatures.items():
                previous_signature = self.metadata_badge_signatures.get(session)
                if previous_signature is None:
                    continue
                for badge in METADATA_BADGES:
                    if self.metadata_badge_change_should_pulse(previous_signature, next_signature, badge):
                        self.metadata_badge_pulse_until.setdefault(session, {})[badge] = now + self.metadata_badge_pulse_seconds()
                        state_changed = True

            if self.metadata_badge_signatures != next_signatures:
                self.metadata_badge_signatures = next_signatures
                state_changed = True

            for session, payload in session_payloads.items():
                badge_times = self.metadata_badge_pulse_until.get(session, {})
                remaining = {
                    badge: max(1, int((until - now) * 1000))
                    for badge, until in badge_times.items()
                    if until > now
                }
                if remaining:
                    payload["metadata_badge_pulse_remaining_ms"] = remaining

            if state_changed:
                self.persist_metadata_badge_state_locked()

    def load_metadata_badge_state(self) -> None:
        state = read_yolomux_state()
        with self.metadata_badge_lock:
            self.metadata_badge_signatures = self.sanitized_metadata_badge_signatures(
                state.get(METADATA_BADGE_SIGNATURES_STATE_KEY)
            )
            self.metadata_badge_pulse_until = self.sanitized_metadata_badge_pulse_until(
                state.get(METADATA_BADGE_PULSE_UNTIL_STATE_KEY)
            )

    def persist_metadata_badge_state_locked(self) -> None:
        update_yolomux_state(
            {
                METADATA_BADGE_SIGNATURES_STATE_KEY: self.metadata_badge_signatures,
                METADATA_BADGE_PULSE_UNTIL_STATE_KEY: self.metadata_badge_pulse_until,
            }
        )

    def sanitized_metadata_badge_signatures(self, value: Any) -> dict[str, dict[str, str]]:
        if not isinstance(value, dict):
            return {}
        clean: dict[str, dict[str, str]] = {}
        for session, badges in value.items():
            if not isinstance(session, str) or not isinstance(badges, dict):
                continue
            clean[session] = {badge: str(badges.get(badge) or "") for badge in METADATA_BADGES}
        return clean

    def sanitized_metadata_badge_pulse_until(self, value: Any) -> dict[str, dict[str, float]]:
        if not isinstance(value, dict):
            return {}
        clean: dict[str, dict[str, float]] = {}
        for session, badges in value.items():
            if not isinstance(session, str) or not isinstance(badges, dict):
                continue
            clean_badges: dict[str, float] = {}
            for badge, pulse_until in badges.items():
                if badge not in METADATA_BADGES or not isinstance(pulse_until, (int, float)):
                    continue
                if pulse_until > 0:
                    clean_badges[badge] = float(pulse_until)
            if clean_badges:
                clean[session] = clean_badges
        return clean

    def metadata_badge_signatures_for_session(self, payload: dict[str, Any]) -> dict[str, str]:
        project = payload.get("project") if isinstance(payload.get("project"), dict) else {}
        git_data = project.get("git") if isinstance(project.get("git"), dict) else {}
        pr = self.metadata_badge_pull_request(project)
        checks = pr.get("checks") if isinstance(pr.get("checks"), dict) else {}
        status = "" if not pr or pr.get("source_only") else self.metadata_badge_status_state(pr)
        check_state = self.metadata_badge_ci_state(checks)
        return {
            "main": "main" if str(git_data.get("branch") or "") in {"main", "master"} else "",
            "pr": str(pr.get("number") or "") if pr else "",
            "status": status,
            "ci": check_state if pr and check_state and check_state != "unknown" else "",
        }

    def metadata_badge_change_should_pulse(self, previous: dict[str, str], next_signature: dict[str, str], badge: str) -> bool:
        previous_value = previous.get(badge, "")
        next_value = next_signature.get(badge, "")
        if previous_value == next_value:
            return False
        if self.metadata_badge_change_is_initial_enrichment(previous, next_signature, badge):
            return False
        if badge == "ci":
            return previous_value in {"", "unknown", "pending", "running"} and next_value in {"passing", "failing"}
        if badge == "status":
            return previous_value in {"open", "draft"} and next_value in {"merged", "closed"}
        if badge == "main":
            return bool(next_value)
        return False

    def metadata_badge_status_state(self, pr: dict[str, Any]) -> str:
        if pr.get("draft") is True:
            return "draft"
        if pr.get("merged") is True or isinstance(pr.get("merged_at"), str):
            return "merged"
        state = pr.get("state")
        if state == "closed":
            return "closed"
        if state == "open":
            return "open"
        return state if isinstance(state, str) and state else "unknown"

    def metadata_badge_ci_state(self, checks: dict[str, Any]) -> str:
        state = str(checks.get("state") or "").strip().lower()
        if state == "success":
            return "passing"
        if state == "failure":
            return "failing"
        return state

    def metadata_badge_change_is_initial_enrichment(self, previous: dict[str, str], next_signature: dict[str, str], badge: str) -> bool:
        previous_pr = previous.get("pr", "")
        next_pr = next_signature.get("pr", "")
        previous_status = previous.get("status", "")
        previous_ci = previous.get("ci", "")
        if previous_status not in {"", "unknown"} or previous_ci:
            return False
        if badge in {"status", "ci"} and previous_pr and previous_pr == next_pr:
            return True
        if badge == "pr" and not previous_pr and next_pr:
            return True
        return False

    def metadata_badge_change_is_cold_cache_degradation(self, previous: dict[str, str], next_signature: dict[str, str]) -> bool:
        previous_pr = previous.get("pr", "")
        next_pr = next_signature.get("pr", "")
        if not previous_pr or previous_pr != next_pr:
            return False
        previous_status = previous.get("status", "")
        next_status = next_signature.get("status", "")
        if previous_status not in {"", "unknown"} and next_status in {"", "unknown"}:
            return True
        return bool(previous.get("ci", "")) and not next_signature.get("ci", "") and next_status in {"", "unknown"}

    def metadata_badge_pull_request(self, project: dict[str, Any]) -> dict[str, Any]:
        pr = project.get("pull_request")
        if isinstance(pr, dict) and pr.get("number"):
            return pr
        git_data = project.get("git") if isinstance(project.get("git"), dict) else {}
        if str(git_data.get("branch") or "") not in {"main", "master"}:
            return {}
        number = pull_request_number_from_subject(str(git_data.get("head") or ""))
        if number is None:
            return {}
        return {
            "number": number,
            "checks": github_checks_unknown(),
            "source_only": True,
        }

    def warm_metadata_cache_async(self, sessions: dict[str, SessionInfo]) -> None:
        with self.metadata_warm_lock:
            if self.metadata_warm_running:
                return
            self.metadata_warm_running = True
        snapshot = dict(sessions)
        worker = threading.Thread(target=self.warm_metadata_cache, args=(snapshot,), daemon=True)
        worker.start()

    def warm_metadata_cache(self, sessions: dict[str, SessionInfo]) -> None:
        try:
            for info in sessions.values():
                session_project_metadata(info, self.metadata_cache, allow_network=True)
        finally:
            with self.metadata_warm_lock:
                self.metadata_warm_running = False

    def tmux_snapshot(self, session: str, lines: int) -> tuple[dict[str, Any], HTTPStatus]:
        if session not in self.sessions:
            return {"error": f"unknown session: {session}"}, HTTPStatus.NOT_FOUND
        sessions, errors = discover_sessions([session])
        info = sessions.get(session)
        target = info.selected_pane.target if info and info.selected_pane else session
        result = tmux(["capture-pane", "-t", target, "-p", "-S", f"-{max(1, min(lines, 1000))}"], timeout=3.0)
        if result.returncode != 0:
            error = (result.stderr or result.stdout or "tmux capture-pane failed").strip()
            return {"session": session, "target": target, "errors": [*errors, error]}, HTTPStatus.INTERNAL_SERVER_ERROR
        return {
            "session": session,
            "target": target,
            "text": result.stdout.rstrip("\n"),
            "errors": errors,
        }, HTTPStatus.OK

    def transcript_tail(self, session: str, lines: int) -> tuple[dict[str, Any], HTTPStatus]:
        if session not in self.sessions:
            return {"error": f"unknown session: {session}"}, HTTPStatus.NOT_FOUND
        sessions, errors = discover_sessions([session])
        info = sessions.get(session)
        if not info or not info.agents:
            return {"session": session, "errors": errors, "error": "no agent transcript found"}, HTTPStatus.NOT_FOUND
        agent = next((item for item in info.agents if item.transcript), info.agents[0])
        if not agent.transcript:
            return {"session": session, "agent": asdict(agent), "errors": errors, "error": agent.error}, HTTPStatus.NOT_FOUND
        path = Path(agent.transcript)
        try:
            text = tail_file_lines(path, min(max(1, lines), MAX_TRANSCRIPT_TAIL_LINES))
        except OSError as exc:
            return {"session": session, "agent": asdict(agent), "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR
        return {
            "session": session,
            "agent": asdict(agent),
            "path": str(path),
            "lines": lines,
            "text": text,
            "errors": errors,
        }, HTTPStatus.OK

    def context_tail(self, session: str, messages: int) -> tuple[dict[str, Any], HTTPStatus]:
        payload, status = self.transcript_tail(session, MAX_TRANSCRIPT_TAIL_LINES)
        if status != HTTPStatus.OK:
            return payload, status
        path = payload.get("path")
        text = payload.get("text")
        if not isinstance(path, str) or not isinstance(text, str):
            return {"session": session, "error": "missing transcript text"}, HTTPStatus.NOT_FOUND
        lines = compact_transcript_lines(text, max(1, min(messages, MAX_COMPACT_TRANSCRIPT_ITEMS)))
        return {
            "session": session,
            "path": path,
            "messages": messages,
            "text": "\n\n".join(lines),
            "agent": payload.get("agent"),
            "errors": payload.get("errors", []),
        }, HTTPStatus.OK

    def context_items(self, session: str, messages: int) -> tuple[dict[str, Any], HTTPStatus]:
        payload, status = self.transcript_tail(session, MAX_TRANSCRIPT_TAIL_LINES)
        if status != HTTPStatus.OK:
            return payload, status
        path = payload.get("path")
        text = payload.get("text")
        if not isinstance(path, str) or not isinstance(text, str):
            return {"session": session, "error": "missing transcript text"}, HTTPStatus.NOT_FOUND
        items = compact_transcript_items(text, max(1, min(messages, MAX_COMPACT_TRANSCRIPT_ITEMS)))
        return {
            "session": session,
            "path": path,
            "messages": messages,
            "items": items,
            "agent": payload.get("agent"),
            "errors": payload.get("errors", []),
        }, HTTPStatus.OK

    def codex_summary_prompt(self, session: str, lookback_seconds: int) -> tuple[dict[str, Any], HTTPStatus]:
        payload, status = self.transcript_tail(session, MAX_TRANSCRIPT_TAIL_LINES)
        if status != HTTPStatus.OK:
            return payload, status
        path = payload.get("path")
        text = payload.get("text")
        if not isinstance(path, str) or not isinstance(text, str):
            return {"session": session, "error": "missing transcript text"}, HTTPStatus.NOT_FOUND

        bounded_lookback = max(60, min(lookback_seconds, 24 * 3600))
        since = datetime.now(timezone.utc) - timedelta(seconds=bounded_lookback)
        items, stats = compact_transcript_items_since(text, since)
        fallback = False
        if not items:
            fallback = True
            items = compact_transcript_items(text, MAX_COMPACT_TRANSCRIPT_ITEMS)

        summary_text = "\n\n".join(format_transcript_item(item) for item in items)
        summary_text, truncated = trim_prompt_text(summary_text, SUMMARY_MAX_PROMPT_CHARS)
        sessions, discovery_errors = discover_sessions(self.sessions)
        focus_root, inventory = project_inventory(sessions, session)
        prompt = codex_summary_prompt(
            session=session,
            transcript_path=path,
            transcript_text=summary_text,
            focus_root=focus_root,
            project_inventory=inventory,
            since=since,
            lookback_seconds=bounded_lookback,
            fallback=fallback,
            truncated=truncated,
            stats=stats,
        )
        return {
            "session": session,
            "path": path,
            "prompt": prompt,
            "since": since.isoformat(),
            "lookback_seconds": bounded_lookback,
            "items": len(items),
            "fallback": fallback,
            "truncated": truncated,
            "stats": stats,
            "focus_root": focus_root,
            "projects": inventory,
            "agent": payload.get("agent"),
            "errors": [*payload.get("errors", []), *discovery_errors],
        }, HTTPStatus.OK

    def summary(self, session: str) -> tuple[dict[str, Any], HTTPStatus]:
        if session not in self.sessions:
            return {"error": f"unknown session: {session}"}, HTTPStatus.NOT_FOUND
        sessions, errors = discover_sessions([session])
        info = sessions.get(session)
        selected = info.selected_pane if info else None
        agent = next((item for item in info.agents if item.transcript), None) if info else None
        if agent is None and info and info.agents:
            agent = info.agents[0]

        lines: list[str] = [f"tmux session: {session}"]
        if selected:
            lines.append(f"active target: {selected.target}")
            lines.append(f"pane: {selected.command} in {selected.current_path}")
            if selected.title:
                lines.append(f"title: {selected.title}")
        else:
            lines.append("active target: not found")
        if agent:
            lines.append(f"agent: {agent.kind} pid={agent.pid} status={agent.status or 'unknown'}")
            if agent.transcript:
                lines.append(f"transcript: {agent.transcript}")
            elif agent.error:
                lines.append(f"transcript: {agent.error}")

        snapshot, snapshot_status = self.tmux_snapshot(session, 12)
        if snapshot_status == HTTPStatus.OK and isinstance(snapshot.get("text"), str):
            visible = [line for line in snapshot["text"].splitlines() if line.strip()]
            if visible:
                lines.append("")
                lines.append("visible terminal tail:")
                lines.extend(f"- {truncate_text(line, 220)}" for line in visible[-6:])

        context, context_status = self.context_tail(session, 8)
        if context_status == HTTPStatus.OK and isinstance(context.get("text"), str):
            recent = compact_summary_lines(context["text"])
            if recent:
                lines.append("")
                lines.append("recent transcript activity:")
                lines.extend(f"- {line}" for line in recent[-8:])
        recent_events = self.event_log.tail(session=session, limit=5)
        if recent_events:
            lines.append("")
            lines.append("recent events:")
            for event in recent_events[-5:]:
                event_time = event.get("time", "")
                event_type = event.get("type", "")
                message = event.get("message", "")
                lines.append(f"- {event_time} {event_type}: {message}".strip())
        if errors:
            lines.append("")
            lines.append("discovery warnings:")
            lines.extend(f"- {error}" for error in errors)
        return {
            "session": session,
            "text": "\n".join(lines),
            "errors": errors,
        }, HTTPStatus.OK

    def tmux_next_window(self, session: str) -> tuple[dict[str, Any], HTTPStatus]:
        if session not in self.sessions:
            return {"error": f"unknown session: {session}"}, HTTPStatus.NOT_FOUND
        result = tmux(["next-window", "-t", tmux_session_target(session)], timeout=3.0)
        if result.returncode != 0:
            error = (result.stderr or result.stdout or "tmux next-window failed").strip()
            return {"session": session, "error": error}, HTTPStatus.INTERNAL_SERVER_ERROR
        return {"session": session, "ok": True}, HTTPStatus.OK

    def stop_auto_approve_worker(self, session: str) -> None:
        with self.auto_workers_lock:
            worker = self.auto_workers.pop(session, None)
        if worker is not None:
            worker.stop()
        self.set_persisted_auto_session(session, False)

    def rename_session(self, session: str, new_name: str) -> tuple[dict[str, Any], HTTPStatus]:
        self.refresh_sessions()
        new_name = str(new_name or "").strip()
        if session not in self.sessions:
            return {"error": f"unknown session: {session}"}, HTTPStatus.NOT_FOUND
        name_error = tmux_session_name_error(new_name)
        if name_error:
            return {"session": session, "new_name": new_name, "error": name_error}, HTTPStatus.BAD_REQUEST
        if new_name != session and new_name in self.sessions:
            return {"session": session, "new_name": new_name, "error": f"session already exists: {new_name}"}, HTTPStatus.CONFLICT
        if new_name == session:
            return {"session": session, "new_session": new_name, "renamed": False, "sessions": self.sessions, "ok": True}, HTTPStatus.OK

        result = tmux(["rename-session", "-t", tmux_session_target(session), new_name], timeout=3.0)
        if result.returncode != 0:
            error = (result.stderr or result.stdout or "tmux rename-session failed").strip()
            return {"session": session, "new_name": new_name, "error": error}, HTTPStatus.INTERNAL_SERVER_ERROR

        self.stop_auto_approve_worker(session)
        self.refresh_sessions()
        self.log_event(new_name, "session_renamed", f"renamed {session} to {new_name}", {"old_session": session, "new_session": new_name})
        return {"session": session, "new_session": new_name, "renamed": True, "sessions": self.sessions, "ok": True}, HTTPStatus.OK

    def kill_session(self, session: str) -> tuple[dict[str, Any], HTTPStatus]:
        self.refresh_sessions()
        if session not in self.sessions:
            return {"error": f"unknown session: {session}"}, HTTPStatus.NOT_FOUND

        result = tmux(["kill-session", "-t", tmux_session_target(session)], timeout=3.0)
        if result.returncode != 0:
            error = (result.stderr or result.stdout or "tmux kill-session failed").strip()
            return {"session": session, "error": error}, HTTPStatus.INTERNAL_SERVER_ERROR

        self.stop_auto_approve_worker(session)
        self.refresh_sessions()
        self.log_event(None, "session_killed", f"killed {session}", {"session": session})
        return {"session": session, "killed": True, "sessions": self.sessions, "ok": True}, HTTPStatus.OK

    def tmux_scroll(self, session: str, direction: str, lines: int) -> None:
        if session not in self.sessions or direction not in {"up", "down"}:
            return
        bounded_lines = str(max(1, min(lines, 80)))
        target = tmux_session_target(session)
        if direction == "up":
            tmux(["copy-mode", "-e", "-t", target], timeout=1.0)
            command = "scroll-up"
        else:
            command = "scroll-down"
        tmux(["send-keys", "-t", target, "-X", "-N", bounded_lines, command], timeout=1.0)

    def ensure_session(self, session: str) -> tuple[dict[str, Any], HTTPStatus]:
        self.refresh_sessions()
        if session not in self.sessions:
            return {"error": f"unknown session: {session}"}, HTTPStatus.NOT_FOUND

        if tmux_has_exact_session(session):
            return {"session": session, "created": False, "ok": True}, HTTPStatus.OK

        self.sessions = [item for item in self.sessions if item != session]
        return {"error": f"session no longer exists: {session}"}, HTTPStatus.NOT_FOUND

    def create_next_session(self, agent: str) -> tuple[dict[str, Any], HTTPStatus]:
        self.refresh_sessions()
        agent = agent if agent in AGENT_COMMANDS else "claude"
        available_agents = available_agent_commands()
        if agent not in available_agents:
            return {
                "error": f"{agent} is not available on this server PATH",
                "agent": agent,
                "available_agents": available_agents,
                "sessions": self.sessions,
            }, HTTPStatus.NOT_FOUND
        if len(self.sessions) >= MAX_YOLOMUX_SESSION_TABS:
            return {
                "error": f"maximum session tabs reached: {MAX_YOLOMUX_SESSION_TABS}",
                "sessions": self.sessions,
            }, HTTPStatus.CONFLICT
        session = next_numbered_session_name(self.sessions)
        if session is None:
            return {
                "error": f"no available numbered session names from 1 to {MAX_YOLOMUX_SESSION_TABS}",
                "sessions": self.sessions,
            }, HTTPStatus.CONFLICT
        cwd = session_workdir(session)
        command = agent_command(agent, self.dangerously_yolo)
        result = tmux(
            [
                "new-session",
                "-d",
                "-s",
                session,
                "-c",
                str(cwd),
                command,
            ],
            timeout=5.0,
        )
        if result.returncode != 0:
            error = (result.stderr or result.stdout or "tmux new-session failed").strip()
            return {"session": session, "created": False, "error": error}, HTTPStatus.INTERNAL_SERVER_ERROR
        self.refresh_sessions()
        self.log_event(
            session,
            "session_started",
            f"created {session} with {agent}",
            {"agent": agent, "cwd": str(cwd), "command": command, "dangerously_yolo": self.dangerously_yolo},
        )
        return {
            "session": session,
            "sessions": self.sessions,
            "agent": agent,
            "created": True,
            "cwd": str(cwd),
            "command": command,
            "dangerously_yolo": self.dangerously_yolo,
            "ok": True,
        }, HTTPStatus.OK

    def upload_files(self, session: str, files: list[UploadedFile]) -> tuple[dict[str, Any], HTTPStatus]:
        if session not in self.sessions:
            return {"error": f"unknown session: {session}"}, HTTPStatus.NOT_FOUND
        if not files:
            return {"session": session, "error": "no files supplied"}, HTTPStatus.BAD_REQUEST
        if len(files) > UPLOAD_MAX_FILES:
            return {
                "session": session,
                "error": f"too many files; limit is {UPLOAD_MAX_FILES}",
            }, HTTPStatus.REQUEST_ENTITY_TOO_LARGE

        target_dir, target_source = self.upload_target_dir(session)
        if target_dir is None:
            return {
                "session": session,
                "error": f"upload target not found for {session}",
                "target_source": target_source,
            }, HTTPStatus.NOT_FOUND
        if not target_dir.is_dir():
            return {"session": session, "error": f"upload target is not a directory: {target_dir}"}, HTTPStatus.NOT_FOUND

        saved: list[dict[str, Any]] = []
        upload_template = settings_payload().get("settings", {}).get("uploads", {}).get("filename_template")
        for upload in files:
            safe_name = sanitize_upload_filename(upload.filename)
            path = unique_upload_path(target_dir, safe_name, str(upload_template or ""))
            try:
                path.write_bytes(upload.content)
            except OSError as exc:
                return {
                    "session": session,
                    "error": f"failed to save {safe_name}: {exc}",
                    "target_dir": str(target_dir),
                }, HTTPStatus.INTERNAL_SERVER_ERROR
            saved.append(
                {
                    "name": upload.filename,
                    "saved_name": path.name,
                    "path": str(path),
                    "size": len(upload.content),
                }
            )
        self.log_event(
            session,
            "upload",
            f"uploaded {len(saved)} file{'s' if len(saved) != 1 else ''}",
            {
                "target_dir": str(target_dir),
                "target_source": target_source,
                "files": [item["path"] for item in saved],
                "sizes": [item["size"] for item in saved],
            },
        )
        return {
            "session": session,
            "target_dir": str(target_dir),
            "target_source": target_source,
            "files": saved,
        }, HTTPStatus.OK

    def upload_max_bytes(self) -> int:
        value = settings_payload().get("settings", {}).get("uploads", {}).get("max_bytes", UPLOAD_MAX_BYTES)
        return int(value) if isinstance(value, (int, float)) and value > 0 else UPLOAD_MAX_BYTES

    def upload_target_dir(self, session: str) -> tuple[Path | None, str]:
        focus_root = focus_root_for_session(session)
        if focus_root:
            return Path(focus_root), "session_workdir"
        workdir = session_workdir(session)
        resolved, ok = resolved_upload_dir(workdir)
        if ok:
            return resolved, "session_workdir"

        sessions, _ = discover_sessions([session])
        info = sessions.get(session)
        if info is None:
            return None, "session_workdir"
        git_data = session_git_inventory(info)
        if git_data is not None:
            for key in ("root", "cwd"):
                value = git_data.get(key)
                if isinstance(value, str):
                    resolved, ok = resolved_upload_dir(Path(value))
                    if ok:
                        return resolved, f"git_{key}"
        for cwd in candidate_session_cwds(info):
            resolved, ok = resolved_upload_dir(Path(cwd), allow_home=True)
            if ok:
                return resolved, "pane_current_path"
        return None, "session_workdir"

    def set_auto_approve(self, session: str, enabled: bool, persist: bool = True, takeover: bool = True) -> tuple[dict[str, Any], HTTPStatus]:
        if session not in self.sessions:
            return {"error": f"unknown session: {session}"}, HTTPStatus.NOT_FOUND

        with self.auto_workers_lock:
            existing = self.auto_workers.get(session)
            if existing and not existing.alive():
                self.auto_workers.pop(session, None)
                existing = None
                if persist:
                    self.persist_auto_sessions()

            if enabled:
                if existing:
                    return self.auto_approve_session_status(session), HTTPStatus.OK
                if not tmux_has_exact_session(session):
                    return {"session": session, "enabled": False, "error": f"tmux session not found: {session}"}, HTTPStatus.NOT_FOUND
                worker, status = self.start_auto_approve_worker(session, takeover=takeover)
                if worker is None:
                    return status, HTTPStatus.CONFLICT
                self.auto_workers[session] = worker
                if persist:
                    self.set_persisted_auto_session(session, True)
                self.log_event(session, "yolo_enabled", "YOLO enabled", {"persist": persist})
                return self.auto_approve_session_status(session), HTTPStatus.OK

            if existing:
                existing.stop()
                self.auto_workers.pop(session, None)
                if persist:
                    self.set_persisted_auto_session(session, False)
                self.log_event(session, "yolo_disabled", "YOLO disabled", {"persist": persist})
        return self.auto_approve_session_status(session), HTTPStatus.OK

    def start_auto_approve_worker(self, session: str, takeover: bool) -> tuple[AutoApproveWorker | None, dict[str, Any]]:
        worker = AutoApproveWorker(
            session,
            interval=self.auto_approve_interval_seconds(),
            event_callback=self.log_auto_event,
            owner_extra=self.control_server.owner_payload(),
            dangerously_yolo=self.dangerously_yolo,
            prompt_source=self.auto_approve_prompt_source(),
        )
        started, owner = worker.start()
        if started:
            return worker, worker.status()
        locked_owner = owner
        if takeover and self.request_auto_approve_release(session, owner):
            worker = AutoApproveWorker(
                session,
                interval=self.auto_approve_interval_seconds(),
                event_callback=self.log_auto_event,
                owner_extra=self.control_server.owner_payload(),
                dangerously_yolo=self.dangerously_yolo,
                prompt_source=self.auto_approve_prompt_source(),
            )
            started, owner = worker.start()
            if started:
                self.log_event(session, "yolo_takeover", "YOLO moved from another server", {"owner": locked_owner or {}})
                return worker, worker.status()
        payload = worker.status()
        payload.update({
            "enabled": False,
            "enabled_elsewhere": True,
            "locked": True,
            "lock_owner": owner,
            "error": auto_approve_lock_message(owner),
        })
        self.log_event(session, "yolo_locked", "YOLO already owned by another server", {"owner": owner or {}})
        return None, payload

    def request_auto_approve_release(self, session: str, owner: dict[str, Any] | None) -> bool:
        request = {
            "action": "disable_auto_approve",
            "session": session,
            "requester": {
                "pid": os.getpid(),
                "hostname": SERVER_HOSTNAME,
                "project_root": str(PROJECT_ROOT),
                "control_socket": str(self.control_server.path),
            },
        }
        response = send_yolomux_control_request(owner, request)
        if response.get("ok") is not True:
            self.log_event(session, "yolo_takeover_failed", "YOLO owner did not release", {"owner": owner or {}, "response": response})
            return False
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if auto_approve_lock_owner(session) is None:
                return True
            time.sleep(0.05)
        self.log_event(session, "yolo_takeover_failed", "YOLO owner kept lock after release request", {"owner": owner or {}, "response": response})
        return False

    def prompt_and_screen_status(self, session: str) -> tuple[dict[str, Any], dict[str, Any]]:
        try:
            module = auto_approve_tmux
            visible_text = module.tmux_capture_pane(session, visible_only=True)
            if visible_text is None:
                prompt = {"visible": False, "type": "", "text": "", "yes_selected": False, "action": "", "error": "failed to capture pane"}
                screen = {"key": "disconnected", "text": "failed to capture pane"}
                return prompt, screen
            prompt_state = module.hybrid_approval_prompt_state(session, visible_text, prompt_source=self.auto_approve_prompt_source())
            if prompt_state.get("visible") and prompt_state.get("type") == "bash":
                pane_text = module.tmux_capture_pane(session)
                prompt_state = module.hybrid_approval_prompt_state(session, visible_text, pane_text or visible_text, prompt_source=self.auto_approve_prompt_source())
            screen_state = module.agent_screen_state(visible_text)
            if screen_state.get("key") == "idle":
                infos, _errors = discover_sessions([session])
                transcript_state = session_transcript_activity_state(infos.get(session))
                if transcript_state.get("key") != "idle":
                    screen_state = transcript_state
            return dict(prompt_state), dict(screen_state)
        except (OSError, subprocess.SubprocessError) as exc:
            prompt = {"visible": False, "type": "", "text": "", "yes_selected": False, "action": "", "error": str(exc)}
            screen = {"key": "error", "text": str(exc)}
            return prompt, screen

    def auto_approve_session_status(self, session: str) -> dict[str, Any]:
        with self.auto_workers_lock:
            worker = self.auto_workers.get(session)
        if worker:
            payload = worker.status()
            payload["enabled_elsewhere"] = False
            payload["locked"] = False
        else:
            payload = {
                "target": session,
                "enabled": False,
                "enabled_elsewhere": False,
                "locked": False,
                "approved": 0,
                "blocked": 0,
                "last_action": "off",
            }
            owner = auto_approve_lock_owner(session)
            if owner:
                payload.update({
                    "enabled_elsewhere": True,
                    "locked": True,
                    "lock_owner": owner,
                    "last_action": auto_approve_lock_message(owner),
                    "error": auto_approve_lock_message(owner),
                })
        prompt, screen = self.prompt_and_screen_status(session)
        payload["prompt"] = prompt
        payload["screen"] = screen
        return payload

    def auto_approve_status(self, session: str | None = None) -> tuple[dict[str, Any], HTTPStatus]:
        if session is not None and session not in self.sessions:
            return {"error": f"unknown session: {session}"}, HTTPStatus.NOT_FOUND
        removed = False
        with self.auto_workers_lock:
            for name, worker in list(self.auto_workers.items()):
                if not worker.alive():
                    self.log_event(name, "worker_stopped", "YOLO worker stopped", worker.status())
                    self.auto_workers.pop(name, None)
                    removed = True
        if removed:
            self.persist_auto_sessions()
        if session is not None:
            return self.auto_approve_session_status(session), HTTPStatus.OK
        refresh_errors = self.refresh_sessions()
        return {
            "session_order": self.sessions,
            "sessions": {name: self.auto_approve_session_status(name) for name in self.sessions},
            "errors": refresh_errors,
            "rules": self.yolo_rules_payload(),
        }, HTTPStatus.OK

    def stop_auto_approve_all(self) -> None:
        with self.auto_workers_lock:
            for worker in list(self.auto_workers.values()):
                worker.stop()
            self.auto_workers.clear()
        self.control_server.stop()
