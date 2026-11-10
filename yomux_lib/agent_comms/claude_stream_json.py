# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Shared Claude Code `--output-format stream-json` process helpers."""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from typing import Any

from .stream_events import ClaudeStreamJsonNormalizer
from .process_stream import process_pipe_output
from .process_stream import retire_process
from .process_stream import close_process_pipes


CLAUDE_STREAM_JSON_DEFAULT_TOOLS = "default"
CLAUDE_STREAM_JSON_PERMISSION_MODE = "bypassPermissions"


def claude_stream_json_env() -> dict[str, str]:
    return {**os.environ, "TERM": "xterm-256color", "NO_COLOR": "1"}


def claude_stream_json_argv(target: dict[str, Any]) -> list[str]:
    args = [
        "claude",
        "-p",
        "--verbose",
        "--input-format",
        "text",
        "--output-format",
        "stream-json",
        "--include-partial-messages",
    ]
    session_id = str(target.get("thread_id") or target.get("agent_session_id") or "").strip()
    if session_id:
        if target.get("resume") is False:
            args.extend(["--session-id", session_id])
        else:
            args.extend(["--resume", session_id])
    model = str(target.get("model") or target.get("agent_model") or "").strip()
    if model:
        args.extend(["--model", model])
    effort = str(target.get("agent_effort") or target.get("effort") or "").strip()
    if effort:
        args.extend(["--effort", effort])
    if "tools" in target:
        tools = str(target.get("tools") or "").strip()
        args.extend(["--tools", tools])
    if "permission_mode" in target or "permissionMode" in target:
        permission_mode = str(target.get("permission_mode") or target.get("permissionMode") or "").strip()
        args.extend(["--permission-mode", permission_mode])
    json_schema = target.get("json_schema")
    if isinstance(json_schema, dict):
        args.extend(["--json-schema", json.dumps(json_schema, ensure_ascii=False, separators=(",", ":"))])
    return args


def claude_stream_json_result(stdout: str) -> tuple[str, str]:
    assistant_parts: list[str] = []
    stream_delta_parts: list[str] = []
    for line in str(stdout or "").splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "")
        if item_type == "assistant":
            message = item.get("message") if isinstance(item.get("message"), dict) else {}
            content = message.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                        assistant_parts.append(block["text"])
        elif item_type == "stream_event":
            event = item.get("event") if isinstance(item.get("event"), dict) else {}
            delta = event.get("delta") if isinstance(event.get("delta"), dict) else {}
            if delta.get("type") == "text_delta" and isinstance(delta.get("text"), str):
                stream_delta_parts.append(delta["text"])
        elif item_type == "result":
            if item.get("is_error"):
                return "", str(item.get("result") or item.get("api_error_status") or "Claude stream-json result error")
            result = str(item.get("result") or "").strip()
            return result or "".join(assistant_parts).strip() or "".join(stream_delta_parts).strip(), ""
    return "".join(assistant_parts).strip() or "".join(stream_delta_parts).strip(), ""


def claude_stream_json_run(
    args: list[str],
    text: str,
    *,
    cwd: str,
    env: dict[str, str],
    timeout: float,
    on_event: Any | None = None,
    popen: Any | None = None,
    cancel_event: threading.Event | None = None,
    process_callback: Any | None = None,
) -> tuple[int, str, str]:
    launch = popen or subprocess.Popen
    process: subprocess.Popen[str] | None = None
    stdout_parts: list[str] = []
    stderr_text = ""
    normalizer = ClaudeStreamJsonNormalizer()
    try:
        process = launch(
            args,
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        if process.stdin is None or process.stdout is None:
            raise OSError("claude stream-json pipes are unavailable")
        if callable(process_callback):
            process_callback(process)
        deadline = time.monotonic() + max(1.0, timeout)
        stderr_parts: list[str] = []
        try:
            with process_pipe_output(process, lambda: deadline, cancel_event=cancel_event, input_text=text) as output:
                for name, line in output:
                    if name == "stderr":
                        stderr_parts.append(line)
                        continue
                    stdout_parts.append(line)
                    if callable(on_event):
                        for event in normalizer.normalize_line(line):
                            on_event(event)
        except TimeoutError as error:
            raise subprocess.TimeoutExpired(args, timeout) from error
        stderr_text = "".join(stderr_parts)
        return process.wait(timeout=max(0.1, deadline - time.monotonic())), "".join(stdout_parts), stderr_text
    finally:
        if process is not None:
            try:
                retire_process(process)
            finally:
                close_process_pipes(process)
