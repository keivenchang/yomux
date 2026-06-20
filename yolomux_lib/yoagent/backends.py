# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Backend selection and launch helpers for Yoagent control flow."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import uuid
from typing import Any

from ..activity_summary import build_yoagent_chat_prompt
from ..activity_summary import build_yoagent_resume_prompt
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
    ttfr_ms = timing.get("ttfr_ms")
    if isinstance(ttfr_ms, (int, float)):
        lines.append(f"- response time: `{float(ttfr_ms) / 1000:.3f}s` (`{float(ttfr_ms):.1f}ms`)")
    elapsed_ms = cli.get("elapsed_ms")
    if isinstance(elapsed_ms, (int, float)):
        lines.append(f"- model CLI time: `{float(elapsed_ms) / 1000:.3f}s`")
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
        if entry.get("installed") and entry.get("logged_in"):
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
        return answer, self.deps.yoagent_cli_fallback_reason(backend, error), {
            "backend": backend,
            "prompt_chars": len(prompt),
            "elapsed_ms": round((time.monotonic() - started) * 1000),
            "direct": True,
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
    ) -> tuple[str, str, dict[str, Any]]:
        if backend not in {"codex", "claude"}:
            return "", f"unknown backend: {backend}", {}

        with self.yoagent_cli_lock:
            state = self.yoagent_cli_sessions.get(backend, {})
            session_id = str(state.get("session_id") or "")
            context_signature = self.deps.yoagent_activity_payload_signature(activity_payload)
            context_changed = context_signature != state.get("activity_signature")
            seed = not session_id
            next_session_id = session_id or (str(uuid.uuid4()) if backend == "claude" else "")
            prompt = build_yoagent_chat_prompt(question, activity_payload, settings, history, locale) if seed else build_yoagent_resume_prompt(question, activity_payload, settings, context_changed, locale)
            prompt += self.deps.yoagent_language_directive(locale)

        started = time.monotonic()
        if stream_id:
            self.publish_yoagent_stream_delta(stream_id, "", backend=backend, phase="started")
        if backend == "codex":
            stream_callback = self.yoagent_stream_callback(stream_id, backend) if stream_id else None
            if stream_callback:
                answer, error, captured_session_id, backend_status = self.deps.run_yoagent_codex_app_server(
                    prompt,
                    session_id=session_id,
                    resume=not seed,
                    settings=settings,
                    stream_callback=stream_callback,
                )
            else:
                answer, error, captured_session_id, backend_status = self.deps.run_yoagent_codex_app_server(prompt, session_id=session_id, resume=not seed, settings=settings)
            next_session_id = captured_session_id or session_id
            if error and not answer:
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
            answer, error = self.deps.run_yoagent_claude_cli(prompt, session_id=next_session_id, resume=not seed, model=claude_model, effort=claude_effort)
            backend_status = {"transport": "claude-cli", "persistent": False, "model": claude_model, "effort": claude_effort or None}
        elapsed_ms = round((time.monotonic() - started) * 1000)
        fallback_reason = self.deps.yoagent_cli_fallback_reason(backend, error)
        status = {
            **backend_status,
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


    def run_yoagent_codex_cli(self, prompt: str, session_id: str = "", resume: bool = False, settings: dict[str, Any] | None = None) -> tuple[str, str, str]:
        if not shutil.which("codex"):
            return "", "codex CLI not found", ""
        current_settings = settings or self.yoagent_settings()
        args = codex_exec_argv(
            resume_session_id=session_id if resume and session_id else None,
            model=str(current_settings.get("codex_model") or "").strip() or None,
            effort=str(current_settings.get("codex_effort") or "").strip() or None,
            service_tier=SUMMARY_CODEX_SERVICE_TIER,
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


    def run_yoagent_claude_cli(self, prompt: str, session_id: str = "", resume: bool = False, model: str = "", effort: str = "") -> tuple[str, str]:
        if not shutil.which("claude"):
            return "", "claude CLI not found"
        args = ["claude", "-p"]
        if model:
            args.extend(["--model", model])
        if effort:
            args.extend(["--effort", effort])
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
