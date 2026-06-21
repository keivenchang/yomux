# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Shared Codex app-server stdio client."""

from __future__ import annotations

import json
import selectors
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any

from ..common import PROJECT_ROOT
from ..common import YOLOMUX_VERSION
from ..common import codex_runtime_env
from .json_rpc import json_rpc_notification
from .json_rpc import json_rpc_request
from .stream_events import ASSISTANT_DELTA
from .stream_events import normalize_codex_app_server_message


CODEX_APP_SERVER_TIMEOUT_SECONDS = 120.0


@dataclass(frozen=True)
class CodexAppServerResult:
    ok: bool
    sent: bool
    text: str = ""
    error: str = ""
    reason_code: str = ""


def codex_app_server_initialize_params() -> dict[str, Any]:
    return {
        "clientInfo": {"name": "yolomux", "title": "YOLOmux", "version": YOLOMUX_VERSION},
        "capabilities": {"experimentalApi": True, "requestAttestation": False},
    }


def codex_app_server_thread_params(target: dict[str, Any]) -> dict[str, Any]:
    params: dict[str, Any] = {
        "approvalPolicy": str(target.get("approval_policy") or target.get("approvalPolicy") or "on-request"),
        "approvalsReviewer": str(target.get("approvals_reviewer") or target.get("approvalsReviewer") or "user"),
        "sandbox": str(target.get("sandbox") or target.get("sandbox_mode") or "read-only"),
        "ephemeral": target.get("ephemeral") is not False,
    }
    cwd = str(target.get("cwd") or PROJECT_ROOT).strip()
    if cwd:
        params["cwd"] = cwd
    model = str(target.get("model") or target.get("agent_model") or "").strip()
    if model:
        params["model"] = model
    base_instructions = str(target.get("base_instructions") or "").strip()
    if base_instructions:
        params["baseInstructions"] = base_instructions
    return params


def codex_app_server_turn_params(thread_id: str, text: str, target: dict[str, Any]) -> dict[str, Any]:
    params: dict[str, Any] = {
        "threadId": thread_id,
        "input": [{"type": "text", "text": text, "text_elements": []}],
    }
    cwd = str(target.get("cwd") or "").strip()
    if cwd:
        params["cwd"] = cwd
    return params


def codex_app_server_turn_text(turn: Any) -> str:
    if not isinstance(turn, dict):
        return ""
    items = turn.get("items")
    if not isinstance(items, list):
        return ""
    parts: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "agentMessage" and isinstance(item.get("text"), str):
            parts.append(item["text"])
    return "\n".join(part.strip() for part in parts if part.strip()).strip()


class CodexAppServerProtocol:
    def write_message(self, process: subprocess.Popen[str], message: dict[str, Any]) -> None:
        if process.stdin is None:
            raise OSError("codex app-server stdin is closed")
        process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
        process.stdin.flush()

    def readline(self, process: subprocess.Popen[str], deadline: float) -> str:
        if process.stdout is None:
            raise OSError("codex app-server stdout is closed")
        timeout = max(0.0, deadline - time.monotonic())
        fileno: int | None = None
        try:
            fileno = process.stdout.fileno()
        except (OSError, ValueError):
            fileno = None
        if fileno is not None:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                if not selector.select(timeout):
                    raise subprocess.TimeoutExpired(["codex", "app-server"], timeout)
        line = process.stdout.readline()
        if not line:
            raise OSError("codex app-server exited without a JSON-RPC message")
        return line

    def read_message(self, process: subprocess.Popen[str], deadline: float) -> dict[str, Any]:
        line = self.readline(process, deadline)
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            raise OSError(f"codex app-server emitted invalid JSON-RPC: {exc}") from exc
        if not isinstance(message, dict):
            raise OSError("codex app-server emitted a non-object JSON-RPC message")
        return message

    def read_response(self, process: subprocess.Popen[str], request_id: str, deadline: float, notifications: list[dict[str, Any]]) -> dict[str, Any]:
        while True:
            message = self.read_message(process, deadline)
            if str(message.get("id") or "") == request_id and ("result" in message or "error" in message):
                if message.get("error"):
                    raise OSError(f"codex app-server {request_id} failed: {message.get('error')}")
                result = message.get("result")
                return result if isinstance(result, dict) else {}
            notifications.append(message)

    def terminate_process(self, process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=1)

    def wait_turn_complete(
        self,
        process: subprocess.Popen[str],
        thread_id: str,
        deadline: float,
        notifications: list[dict[str, Any]],
        on_event: Any | None = None,
    ) -> tuple[str, str]:
        deltas: list[str] = []
        while True:
            message = self.read_message(process, deadline)
            notifications.append(message)
            method = str(message.get("method") or "")
            params = message.get("params") if isinstance(message.get("params"), dict) else {}
            if message.get("id") is not None and method:
                if callable(on_event):
                    for event in normalize_codex_app_server_message(message):
                        on_event(event)
                return "", f"codex app-server requested client handling for `{method}`; approval relay is not implemented yet"
            if callable(on_event):
                for event in normalize_codex_app_server_message(message):
                    on_event(event)
            if method == "item/agentMessage/delta":
                delta = params.get("delta")
                if isinstance(delta, str):
                    deltas.append(delta)
            elif method in {"item/reasoning/delta", "item/thinking/delta", "item/thought/delta"}:
                pass
            elif method == "turn/completed":
                if str(params.get("threadId") or "") != thread_id:
                    continue
                final_text = codex_app_server_turn_text(params.get("turn"))
                return final_text or "".join(deltas).strip(), ""
            elif method == "thread/status/changed":
                status = params.get("status") if isinstance(params.get("status"), dict) else {}
                message_thread_id = str(params.get("threadId") or "")
                if status.get("type") == "idle" and (not message_thread_id or message_thread_id == thread_id):
                    return "".join(deltas).strip(), ""


class CodexAppServerSession:
    """Persistent stdio client for `codex app-server`."""

    def __init__(
        self,
        target: dict[str, Any],
        *,
        popen: Any | None = None,
        protocol: CodexAppServerProtocol | None = None,
    ):
        self.target = dict(target)
        self.popen = popen or subprocess.Popen
        self.protocol = protocol or CodexAppServerProtocol()
        self.process: subprocess.Popen[str] | None = None
        self.thread_id = ""
        self.request_counters: dict[str, int] = {}
        self.lock = threading.RLock()
        self.interrupted = threading.Event()
        self.started_ts = 0.0

    def _request_id(self, prefix: str) -> str:
        self.request_counters[prefix] = self.request_counters.get(prefix, 0) + 1
        return f"{prefix}-{self.request_counters[prefix]}"

    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def close(self) -> None:
        with self.lock:
            if self.process is not None:
                self.protocol.terminate_process(self.process)
            self.process = None

    def interrupt(self) -> dict[str, Any]:
        self.interrupted.set()
        process = self.process
        if process is None:
            return {"ok": True, "interrupted": False, "reason": "no codex app-server process"}
        try:
            self.protocol.terminate_process(process)
        except (OSError, subprocess.SubprocessError) as exc:
            return {"ok": False, "interrupted": False, "error": str(exc)}
        return {"ok": True, "interrupted": True, "transport": "codex-app-server"}

    def _start_process(self, target: dict[str, Any], deadline: float, notifications: list[dict[str, Any]]) -> dict[str, Any]:
        cwd = str(target.get("cwd") or PROJECT_ROOT)
        args = ["codex", "app-server", "--listen", "stdio://"]
        effort = str(target.get("agent_effort") or target.get("effort") or "").strip()
        if effort:
            args.extend(["-c", f'model_reasoning_effort="{effort}"'])
        args.extend(["-c", 'model_reasoning_summary="auto"'])
        service_tier = str(target.get("service_tier") or "").strip()
        if service_tier:
            args.extend(["-c", f'service_tier="{service_tier}"'])
        self.process = self.popen(
            args,
            cwd=cwd,
            env=codex_runtime_env(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self.started_ts = time.time()
        initialize_id = self._request_id("initialize")
        self.protocol.write_message(self.process, json_rpc_request(initialize_id, "initialize", codex_app_server_initialize_params()))
        self.protocol.read_response(self.process, initialize_id, deadline, notifications)
        self.protocol.write_message(self.process, json_rpc_notification("initialized"))
        return {"process_started": True, "process_reused": False}

    def ensure_started(self, target: dict[str, Any] | None = None, *, timeout: float = CODEX_APP_SERVER_TIMEOUT_SECONDS) -> tuple[str, dict[str, Any]]:
        with self.lock:
            if target is not None:
                self.target = dict(target)
            deadline = time.monotonic() + max(1.0, float(timeout))
            notifications: list[dict[str, Any]] = []
            status: dict[str, Any] = {
                "transport": "codex-app-server",
                "persistent": True,
                "process_started": False,
                "process_reused": False,
                "thread_started": False,
                "thread_resumed": False,
            }
            requested_thread = str(self.target.get("thread_id") or self.target.get("agent_session_id") or "").strip()
            if requested_thread and self.thread_id and requested_thread != self.thread_id:
                self.close()
                self.thread_id = ""
            if not self.alive():
                status.update(self._start_process(self.target, deadline, notifications))
            else:
                status["process_reused"] = True
            if self.thread_id and not status["process_started"]:
                status["thread_id"] = self.thread_id
                return self.thread_id, status
            if self.thread_id and not requested_thread:
                requested_thread = self.thread_id
            thread_response: dict[str, Any]
            if requested_thread:
                thread_id = self._request_id("thread")
                try:
                    params = {"threadId": requested_thread, **codex_app_server_thread_params(self.target)}
                    self.protocol.write_message(self.process, json_rpc_request(thread_id, "thread/resume", params))
                    thread_response = self.protocol.read_response(self.process, thread_id, deadline, notifications)
                    status["thread_resumed"] = True
                except OSError as exc:
                    status["resume_error"] = str(exc)
                    requested_thread = ""
            if not requested_thread:
                thread_id = self._request_id("thread")
                self.protocol.write_message(self.process, json_rpc_request(thread_id, "thread/start", codex_app_server_thread_params(self.target)))
                thread_response = self.protocol.read_response(self.process, thread_id, deadline, notifications)
                status["thread_started"] = True
            thread = thread_response.get("thread") if isinstance(thread_response.get("thread"), dict) else {}
            self.thread_id = str(thread.get("id") or thread_response.get("threadId") or thread_response.get("id") or requested_thread or "").strip()
            if not self.thread_id:
                raise OSError("codex app-server did not return a thread id")
            status["thread_id"] = self.thread_id
            return self.thread_id, status

    def send(
        self,
        text: str,
        target: dict[str, Any] | None = None,
        *,
        timeout: float = CODEX_APP_SERVER_TIMEOUT_SECONDS,
        on_event: Any | None = None,
    ) -> tuple[CodexAppServerResult, dict[str, Any]]:
        with self.lock:
            status: dict[str, Any] = {}
            turn_started = False
            started = time.monotonic()
            try:
                self.interrupted.clear()
                deadline = time.monotonic() + max(1.0, float(timeout))
                thread_id, status = self.ensure_started(target, timeout=timeout)
                status["thread_ready_ms"] = round((time.monotonic() - started) * 1000, 3)
                notifications: list[dict[str, Any]] = []
                turn_id = self._request_id("turn")
                first_event_seen = False
                first_assistant_delta_seen = False

                def timed_on_event(event: dict[str, Any]) -> None:
                    nonlocal first_event_seen, first_assistant_delta_seen
                    now = time.monotonic()
                    if not first_event_seen:
                        first_event_seen = True
                        status["first_stream_event_ms"] = round((now - started) * 1000, 3)
                    if not first_assistant_delta_seen and str(event.get("kind") or event.get("event") or "") == ASSISTANT_DELTA:
                        first_assistant_delta_seen = True
                        status["first_assistant_delta_ms"] = round((now - started) * 1000, 3)
                    if callable(on_event):
                        on_event(event)

                status["turn_start_request_ms"] = round((time.monotonic() - started) * 1000, 3)
                self.protocol.write_message(self.process, json_rpc_request(turn_id, "turn/start", codex_app_server_turn_params(thread_id, text, self.target)))
                self.protocol.read_response(self.process, turn_id, deadline, notifications)
                status["turn_start_ack_ms"] = round((time.monotonic() - started) * 1000, 3)
                turn_started = True
                final_text, error = self.protocol.wait_turn_complete(self.process, thread_id, deadline, notifications, on_event=timed_on_event)
                status["turn_complete_ms"] = round((time.monotonic() - started) * 1000, 3)
                if error:
                    return CodexAppServerResult(ok=False, sent=True, error=error), status
                if final_text:
                    return CodexAppServerResult(ok=True, sent=True, text=final_text), status
                return CodexAppServerResult(ok=False, sent=True, error="codex app-server completed without a final agent message"), status
            except (OSError, subprocess.SubprocessError) as exc:
                self.close()
                if self.interrupted.is_set():
                    return CodexAppServerResult(ok=False, sent=turn_started, error="interrupted", reason_code="interrupted"), status
                return CodexAppServerResult(ok=False, sent=turn_started, error=str(exc)), status
