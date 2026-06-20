# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Shared Claude Code `--output-format stream-json` process helpers."""

from __future__ import annotations

import json
import os
import selectors
import subprocess
import threading
import time
from typing import Any

from .exceptions import TransportInterrupted
from .stream_events import ClaudeStreamJsonNormalizer


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
    permission_mode = str(target.get("permission_mode") or target.get("permissionMode") or "").strip()
    if permission_mode:
        args.extend(["--permission-mode", permission_mode])
    return args


def claude_stream_json_result(stdout: str) -> tuple[str, str]:
    assistant_parts: list[str] = []
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
        elif item_type == "result":
            if item.get("is_error"):
                return "", str(item.get("result") or item.get("api_error_status") or "Claude stream-json result error")
            result = str(item.get("result") or "").strip()
            return result or "".join(assistant_parts).strip(), ""
    return "".join(assistant_parts).strip(), ""


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
        process.stdin.write(text)
        process.stdin.close()
        deadline = time.monotonic() + max(1.0, timeout)
        selector = selectors.DefaultSelector()
        try:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    raise TransportInterrupted("interrupted")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(args, timeout)
                ready = selector.select(timeout=min(0.25, remaining))
                for key, _mask in ready:
                    line = key.fileobj.readline()
                    if line:
                        stdout_parts.append(line)
                        if callable(on_event):
                            for event in normalizer.normalize_line(line):
                                on_event(event)
                if process.poll() is not None:
                    for line in process.stdout.readlines():
                        stdout_parts.append(line)
                        if callable(on_event):
                            for event in normalizer.normalize_line(line):
                                on_event(event)
                    break
        finally:
            selector.close()
        if process.stderr is not None:
            stderr_text = process.stderr.read()
        return process.wait(timeout=max(0.1, deadline - time.monotonic())), "".join(stdout_parts), stderr_text
    except BaseException:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)
        raise

