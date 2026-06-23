# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Backend selection and launch helpers for Yoagent control flow."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from typing import Any

from ..activity_summary import build_yoagent_chat_prompt
from ..activity_summary import build_yoagent_resume_prompt
from ..activity_summary import yoagent_question_requests_session_list
from ..agent_comms.claude_stream_json import CLAUDE_STREAM_JSON_DEFAULT_TOOLS
from ..agent_comms.claude_stream_json import CLAUDE_STREAM_JSON_PERMISSION_MODE
from ..common import PROJECT_ROOT
from ..common import SUMMARY_CODEX_SERVICE_TIER
from ..common import YOAGENT_CLAUDE_SUMMARY_MODEL
from ..common import codex_exec_argv
from ..common import codex_runtime_env
from ..common import truncate_text
from ..transcripts import codex_event_text
from ..web import server_string
from ..workdir import AGENT_LOGIN_COMMANDS
from ..workdir import agent_auth_status
from . import conversation as yoagent_conversation
from .transports import ClaudeStreamJsonTransport
from .transports import CodexAppServerSession


YOAGENT_CLI_TIMEOUT_SECONDS = 45
YOAGENT_STARTUP_QUESTION = (
    "The user just opened YO" + chr(33) + "agent. Read the supplied activity context and give a concise first "
    "assistant response: what looks active, what may need attention, and one concrete next step. "
    "Keep it short and answer as YO" + chr(33) + "agent."
)
YOAGENT_AUTH_FAILURE_RE = re.compile(
    r"(not\s+logged\s+in|log\s*in|login|required\s+auth|authentication|unauthorized|permission\s+denied|401)",
    re.IGNORECASE,
)
YOAGENT_THINK_BLOCK_RE = re.compile(r"<think\b[^>]*>.*?</think>", re.IGNORECASE | re.DOTALL)


def yoagent_cli_auth_failure(text: str) -> bool:
    return bool(YOAGENT_AUTH_FAILURE_RE.search(text or ""))


def strip_yoagent_hidden_thinking(text: str) -> tuple[str, bool]:
    value = str(text or "")
    cleaned, count = YOAGENT_THINK_BLOCK_RE.subn("", value)
    return cleaned.strip(), count > 0


def strip_yoagent_stream_hidden_thinking(text: str) -> tuple[str, bool]:
    value = str(text or "")
    cleaned, count = YOAGENT_THINK_BLOCK_RE.subn("", value)
    if not count and not re.search(r"</?think\b", cleaned, re.IGNORECASE):
        return cleaned, False
    open_think = re.search(r"<think\b[^>]*>", cleaned, re.IGNORECASE)
    if open_think:
        return cleaned[: open_think.start()].strip(), True
    cleaned, close_count = re.subn(r"</think>", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip(), bool(count or close_count)


def yoagent_response_details(response: dict[str, Any]) -> str:
    timing = response.get("timing") if isinstance(response.get("timing"), dict) else {}
    cli = response.get("cli") if isinstance(response.get("cli"), dict) else {}
    lines: list[str] = []
    backend_used = str(response.get("backend_used") or response.get("backend") or "").strip()
    if backend_used:
        lines.append(f"- backend: `{backend_used}`")
    response_ms = yoagent_response_ms(response)
    if response_ms is not None:
        lines.append(f"- response time: `{response_ms / 1000:.3f}s` (`{response_ms:.1f}ms`)")
    elapsed_ms = cli.get("elapsed_ms")
    if isinstance(elapsed_ms, (int, float)):
        lines.append(f"- model CLI time: `{float(elapsed_ms) / 1000:.3f}s`")
    transport = str(cli.get("transport") or "").strip()
    if transport == "codex-app-server":
        warm_state = "warm reuse" if cli.get("process_reused") else "cold start" if cli.get("process_started") else "ready"
        thread_state = "thread resumed" if cli.get("thread_resumed") else "thread started" if cli.get("thread_started") else "thread reused"
        lines.append(f"- Codex transport: `{warm_state}`, `{thread_state}`")
        timing_parts: list[str] = []
        for key, label in (
            ("thread_ready_ms", "thread ready"),
            ("turn_start_ack_ms", "turn ack"),
            ("first_stream_event_ms", "first event"),
            ("first_assistant_delta_ms", "first answer delta"),
            ("turn_complete_ms", "complete"),
        ):
            value = cli.get(key)
            if isinstance(value, (int, float)):
                timing_parts.append(f"{label} {float(value):.1f}ms")
        if timing_parts:
            lines.append(f"- Codex timing: `{'; '.join(timing_parts)}`")
    prompt_chars = cli.get("prompt_chars")
    if isinstance(prompt_chars, int):
        lines.append(f"- prompt size: `{prompt_chars}` chars")
    if "resumed" in cli:
        lines.append(f"- model session: `{'resumed' if cli.get('resumed') else 'seeded'}`")
    if cli.get("context_changed"):
        lines.append("- activity context changed before this model call")
    fallback_reason = str(response.get("fallback_reason") or "").strip()
    if fallback_reason:
        lines.append(f"- fallback reason: {fallback_reason}")
    if response.get("hidden_thinking_removed"):
        lines.append("- raw model thinking was hidden; YOLOmux shows safe diagnostics instead of chain-of-thought")
    return "\n".join(lines)


def yoagent_response_ms(response: dict[str, Any]) -> float | None:
    timing = response.get("timing") if isinstance(response.get("timing"), dict) else {}
    value = timing.get("ttfr_ms")
    if isinstance(value, (int, float)) and float(value) > 0:
        return float(value)
    return None


def yoagent_cli_fallback_reason(backend: str, error: str) -> str:
    text = truncate_text(" ".join(str(error or "").split()), 600)
    if not text:
        return ""
    if not yoagent_cli_auth_failure(text):
        return text
    label = "Claude CLI" if backend == "claude" else "Codex CLI" if backend == "codex" else f"{backend} CLI"
    # Use the canonical login command (verified `claude auth login`, not `claude login`).
    login_command = AGENT_LOGIN_COMMANDS.get(backend, f"{backend} login")
    return f"{label} is not logged in. Run `{login_command}`; showing the No agent YO!agent summary."


def yoagent_language_directive(locale: str) -> str:
    locale_id = str(locale or "").strip()
    if locale_id in {"", "en", "en-XA", "system"}:
        return ""
    directive = server_string(locale_id, "yoagent.prompt.answerLanguage").strip()
    return f"\n\n{directive}" if directive else ""


def resolve_yoagent_backend(backend: str, auth_status: dict[str, dict[str, Any]] | None = None) -> str:
    # the default backend is "auto" — prefer codex, then claude, falling back to the
    # deterministic ("No agent") summary if neither is installed AND logged in. Explicit choices
    # (claude / codex / deterministic) pass through unchanged.
    if backend != "auto":
        return backend
    status = agent_auth_status() if auth_status is None else auth_status
    for agent in ("codex", "claude"):
        entry = status.get(agent, {})
        if entry.get("installed") and entry.get("logged_in") is not False:
            return agent
    return "deterministic"


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


class YoagentBackendsMixin:
    def yoagent_codex_app_server_target(self, settings: dict[str, Any] | None = None) -> dict[str, Any]:
        current_settings = settings or self.yoagent_settings()
        model = str(current_settings.get("codex_model") or "").strip()
        effort = str(current_settings.get("codex_effort") or "").strip()
        target: dict[str, Any] = {
            "session": "__yoagent_codex__",
            "agent_kind": "codex",
            "transport": "codex-app-server",
            "managed": True,
            "cwd": str(PROJECT_ROOT),
            "sandbox": "read-only",
            "approval_policy": "never",
            "approvals_reviewer": "user",
            "ephemeral": False,
            "service_tier": SUMMARY_CODEX_SERVICE_TIER,
        }
        if model:
            target["agent_model"] = model
        if effort:
            target["agent_effort"] = effort
        return target


    def yoagent_codex_app_server_target_key(self, target: dict[str, Any]) -> str:
        return json.dumps(
            {
                "cwd": str(target.get("cwd") or ""),
                "model": str(target.get("agent_model") or target.get("model") or ""),
                "effort": str(target.get("agent_effort") or target.get("effort") or ""),
                "service_tier": str(target.get("service_tier") or ""),
                "sandbox": str(target.get("sandbox") or target.get("sandbox_mode") or ""),
                "approval_policy": str(target.get("approval_policy") or target.get("approvalPolicy") or ""),
            },
            sort_keys=True,
        )


    def close_yoagent_codex_app_server(self) -> None:
        with self.yoagent_codex_app_server_lock:
            if self.yoagent_codex_app_server is not None:
                self.yoagent_codex_app_server.close()
            self.yoagent_codex_app_server = None
            self.yoagent_codex_app_server_key = ""


    def ensure_yoagent_codex_app_server(self, settings: dict[str, Any] | None = None, session_id: str = "") -> tuple[str, str, dict[str, Any]]:
        if not shutil.which("codex"):
            return "", "codex CLI not found", {"transport": "codex-app-server", "persistent": True}
        target = self.deps.yoagent_codex_app_server_target(settings)
        if session_id:
            target["agent_session_id"] = session_id
        key = self.deps.yoagent_codex_app_server_target_key(target)
        started = time.monotonic()
        with self.yoagent_codex_app_server_lock:
            if self.yoagent_codex_app_server is None or self.yoagent_codex_app_server_key != key:
                if self.yoagent_codex_app_server is not None:
                    self.yoagent_codex_app_server.close()
                self.yoagent_codex_app_server = CodexAppServerSession(target)
                self.yoagent_codex_app_server_key = key
            try:
                thread_id, status = self.yoagent_codex_app_server.ensure_started(target, timeout=YOAGENT_CLI_TIMEOUT_SECONDS)
            except (OSError, subprocess.SubprocessError) as exc:
                self.deps.close_yoagent_codex_app_server()
                return "", str(exc), {"transport": "codex-app-server", "persistent": True}
        status["elapsed_ms"] = round((time.monotonic() - started) * 1000)
        return thread_id, "", status


    def run_yoagent_codex_app_server(
        self,
        prompt: str,
        session_id: str = "",
        resume: bool = False,
        settings: dict[str, Any] | None = None,
        stream_callback: Any | None = None,
        request_id: str = "",
    ) -> tuple[str, str, str, dict[str, Any]]:
        if not shutil.which("codex"):
            return "", "codex CLI not found", "", {"transport": "codex-app-server", "persistent": True}
        target = self.deps.yoagent_codex_app_server_target(settings)
        if resume and session_id:
            target["agent_session_id"] = session_id
        key = self.deps.yoagent_codex_app_server_target_key(target)
        started = time.monotonic()
        with self.yoagent_codex_app_server_lock:
            if self.yoagent_codex_app_server is None or self.yoagent_codex_app_server_key != key:
                if self.yoagent_codex_app_server is not None:
                    self.yoagent_codex_app_server.close()
                self.yoagent_codex_app_server = CodexAppServerSession(target)
                self.yoagent_codex_app_server_key = key
            if request_id:
                session = self.yoagent_codex_app_server
                self.deps.set_yoagent_chat_request_interrupt(request_id, session.interrupt)
            result, status = self.yoagent_codex_app_server.send(prompt, target, timeout=YOAGENT_CLI_TIMEOUT_SECONDS, on_event=stream_callback)
            captured_session_id = self.yoagent_codex_app_server.thread_id
        status["elapsed_ms"] = round((time.monotonic() - started) * 1000)
        if result.ok and result.text:
            return result.text, "", captured_session_id, status
        return "", result.error or "codex app-server completed without a final agent message", captured_session_id, status


    def run_yoagent_direct_prompt_backend(self, backend: str, prompt: str, settings: dict[str, Any] | None = None) -> tuple[str, str, dict[str, Any]]:
        if backend not in {"codex", "claude"}:
            return "", f"unknown backend: {backend}", {}
        started = time.monotonic()
        current_settings = settings or self.yoagent_settings()
        if backend == "codex":
            answer, error, _session_id = self.deps.run_yoagent_codex_cli(prompt, session_id="", resume=False, settings=current_settings)
        else:
            claude_model = str(current_settings.get("claude_model") or YOAGENT_CLAUDE_SUMMARY_MODEL).strip()
            claude_effort = str(current_settings.get("claude_effort") or "").strip()
            answer, error = self.deps.run_yoagent_claude_cli(prompt, session_id="", resume=False, model=claude_model, effort=claude_effort)
            tools = CLAUDE_STREAM_JSON_DEFAULT_TOOLS
            permission_mode = CLAUDE_STREAM_JSON_PERMISSION_MODE
        return answer, self.deps.yoagent_cli_fallback_reason(backend, error), {
            "backend": backend,
            "prompt_chars": len(prompt),
            "elapsed_ms": round((time.monotonic() - started) * 1000),
            "direct": True,
            **({"tools": tools, "permission_mode": permission_mode, "external_tools_enabled": True} if backend == "claude" else {}),
        }


    def run_yoagent_cli_backend(
        self,
        backend: str,
        question: str,
        activity_payload: dict[str, Any],
        settings: dict[str, Any],
        history: list[dict[str, str]],
        locale: str = "en",
        stream_id: str = "",
        request_id: str = "",
        include_activity_context: bool = True,
        require_external_tools: bool = False,
    ) -> tuple[str, str, dict[str, Any]]:
        if backend not in {"codex", "claude"}:
            return "", f"unknown backend: {backend}", {}

        with self.yoagent_cli_lock:
            state = self.yoagent_cli_sessions.get(backend, {})
            session_id = str(state.get("session_id") or "")
            context_signature = self.deps.yoagent_activity_payload_signature(activity_payload) if include_activity_context else ""
            context_seen_signature = str(state.get("context_injected_signature") or "")
            context_seen_this_process = bool(context_signature and context_seen_signature == context_signature)
            context_forced = include_activity_context and (
                backend == "claude"
                or yoagent_question_requests_session_list(question)
                or (bool(session_id) and not context_seen_this_process)
            )
            context_changed = include_activity_context and (context_signature != state.get("activity_signature") or context_forced)
            force_seed = backend == "codex" and require_external_tools
            seed = force_seed or not session_id
            next_session_id = session_id or (str(uuid.uuid4()) if backend == "claude" else "")
            prompt_activity = activity_payload if include_activity_context else {}
            prompt_context_included = include_activity_context and (seed or context_changed)
            prompt = build_yoagent_chat_prompt(question, prompt_activity, settings, history, locale) if seed else build_yoagent_resume_prompt(question, prompt_activity, settings, prompt_context_included, locale)
            prompt += self.deps.yoagent_language_directive(locale)

        started = time.monotonic()
        if stream_id:
            self.publish_yoagent_stream_delta(stream_id, "", backend=backend, phase="started")
        if backend == "codex":
            stream_callback = self.yoagent_stream_callback(stream_id, backend) if stream_id else None
            if require_external_tools:
                answer, error, captured_session_id = self.deps.run_yoagent_codex_cli(prompt, session_id="", resume=False, settings=settings, enable_search=True)
                backend_status = {"transport": "codex-exec", "persistent": False, "external_tools_enabled": True, "web_search_enabled": True}
            elif stream_callback:
                answer, error, captured_session_id, backend_status = self.deps.run_yoagent_codex_app_server(
                    prompt,
                    session_id=session_id,
                    resume=not seed,
                    settings=settings,
                    stream_callback=stream_callback,
                    request_id=request_id,
                )
            else:
                answer, error, captured_session_id, backend_status = self.deps.run_yoagent_codex_app_server(prompt, session_id=session_id, resume=not seed, settings=settings, request_id=request_id)
            next_session_id = captured_session_id or session_id
            if request_id and self.deps.yoagent_chat_request_cancelled(request_id):
                backend_status["cancelled"] = True
                error = "interrupted"
            elif error and not answer and not require_external_tools:
                fallback_answer, fallback_error, fallback_session_id = self.deps.run_yoagent_codex_cli(prompt, session_id=session_id, resume=not seed, settings=settings)
                backend_status["fast_backend_error"] = error
                backend_status["fallback_transport"] = "codex-exec"
                if fallback_answer:
                    answer = fallback_answer
                    error = ""
                    next_session_id = fallback_session_id or next_session_id
                    backend_status["transport"] = "codex-exec"
                    backend_status["persistent"] = False
                else:
                    error = fallback_error or error
        else:
            claude_model = str(settings.get("claude_model") or YOAGENT_CLAUDE_SUMMARY_MODEL).strip()
            claude_effort = str(settings.get("claude_effort") or "").strip()
            stream_callback = self.yoagent_stream_callback(stream_id, backend) if stream_id else None
            cancel_event = self.deps.yoagent_chat_request_cancel_event(request_id) if request_id else None
            tools = CLAUDE_STREAM_JSON_DEFAULT_TOOLS
            permission_mode = CLAUDE_STREAM_JSON_PERMISSION_MODE
            answer, error = self.deps.run_yoagent_claude_cli(prompt, session_id=next_session_id, resume=not seed, model=claude_model, effort=claude_effort, stream_callback=stream_callback, request_id=request_id, cancel_event=cancel_event, tools=tools, permission_mode=permission_mode)
            backend_status = {"transport": "claude-stream-json", "persistent": False, "model": claude_model, "effort": claude_effort or None, "external_tools_enabled": True, "tools": tools, "permission_mode": permission_mode}
        elapsed_ms = round((time.monotonic() - started) * 1000)
        fallback_reason = self.deps.yoagent_cli_fallback_reason(backend, error)
        status = {
            **backend_status,
            "backend": backend,
            "resumed": not seed,
            "seeded": seed,
            "context_changed": context_changed,
            "activity_context_included": include_activity_context,
            "activity_context_sent": prompt_context_included,
            "activity_context_forced": context_forced,
            "external_tools_required": bool(require_external_tools),
            "prompt_chars": len(prompt),
            "elapsed_ms": elapsed_ms,
            "session_id": next_session_id or None,
            "per_server": True,
        }
        with self.yoagent_cli_lock:
            if answer and next_session_id and not (backend == "codex" and require_external_tools):
                self.yoagent_cli_sessions[backend] = {
                    "session_id": next_session_id,
                    "activity_signature": context_signature,
                    "context_injected_signature": context_signature if prompt_context_included and context_signature else str(state.get("context_injected_signature") or ""),
                    "updated_ts": time.time(),
                    "updated_monotonic": time.monotonic(),
                }
                yoagent_conversation.save_cli_sessions(self.yoagent_cli_sessions)
            elif fallback_reason:
                self.yoagent_cli_sessions.pop(backend, None)
                yoagent_conversation.save_cli_sessions(self.yoagent_cli_sessions)
        if stream_id:
            visible_answer, hidden_thinking_removed = strip_yoagent_hidden_thinking(answer)
            self.publish_yoagent_stream_delta(
                stream_id,
                visible_answer,
                backend=backend,
                phase="done",
                done=True,
                hidden_thinking_removed=hidden_thinking_removed,
            )
        return answer, fallback_reason, status


    def run_yoagent_codex_cli(self, prompt: str, session_id: str = "", resume: bool = False, settings: dict[str, Any] | None = None, enable_search: bool = False) -> tuple[str, str, str]:
        if not shutil.which("codex"):
            return "", "codex CLI not found", ""
        current_settings = settings or self.yoagent_settings()
        args = codex_exec_argv(
            resume_session_id=session_id if resume and session_id else None,
            model=str(current_settings.get("codex_model") or "").strip() or None,
            effort=str(current_settings.get("codex_effort") or "").strip() or None,
            service_tier=SUMMARY_CODEX_SERVICE_TIER,
            search=enable_search and not (resume and session_id),
        )
        try:
                completed = subprocess.run(
                    args,
                    input=prompt,
                    cwd=str(PROJECT_ROOT),
                    env=codex_runtime_env(),
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
            captured_session_id = captured_session_id or self.deps.codex_event_session_id(event)
            text = codex_event_text(event)
            if text:
                text_parts.append(text)
        if text_parts:
            return "\n".join(text_parts).strip(), "", captured_session_id
        error = completed.stderr.strip() or f"codex exited {completed.returncode}"
        return "", error, captured_session_id


    def run_yoagent_claude_cli(
        self,
        prompt: str,
        session_id: str = "",
        resume: bool = False,
        model: str = "",
        effort: str = "",
        stream_callback: Any | None = None,
        request_id: str = "",
        cancel_event: threading.Event | None = None,
        tools: str = CLAUDE_STREAM_JSON_DEFAULT_TOOLS,
        permission_mode: str = CLAUDE_STREAM_JSON_PERMISSION_MODE,
    ) -> tuple[str, str]:
        if not shutil.which("claude"):
            return "", "claude CLI not found"
        target = {
            "session": session_id or "yoagent-claude",
            "agent_kind": "claude",
            "transport": "claude-stream-json",
            "managed": True,
            "cwd": str(PROJECT_ROOT),
            "agent_session_id": session_id,
            "resume": bool(resume),
        }
        if model:
            target["agent_model"] = model
        if effort:
            target["agent_effort"] = effort
        if tools:
            target["tools"] = tools
        if permission_mode:
            target["permission_mode"] = permission_mode
        result = ClaudeStreamJsonTransport().send(
            target,
            prompt,
            timeout=YOAGENT_CLI_TIMEOUT_SECONDS,
            on_event=stream_callback,
            cancel_event=cancel_event,
            process_callback=(lambda process: self.deps.set_yoagent_chat_request_interrupt(request_id, lambda: self.deps.interrupt_yoagent_claude_process(process))) if request_id else None,
        )
        if result.ok and result.text:
            return result.text, ""
        return "", result.error or "claude stream-json completed without a final response"
