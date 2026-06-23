#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Text client prototype for Claude Code.

This uses Claude Code's structured print mode instead of scraping the terminal UI:
each turn runs `claude -p --verbose --output-format stream-json`, streams text
deltas to stdout, prints tool/Claude thinking metadata in gray, captures the returned
session id, and resumes that session on later turns.

Usage:
  python3 tools/claude.py
  python3 tools/claude.py -C . --model sonnet "summarize this repo"
  python3 tools/claude.py -C . --resume <session-id>
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from yolomux_lib.agent_comms.stream_events import ClaudeStreamJsonNormalizer
import mock_agent_common
from text_client_common import (
    CLAUDE_CONFIG_KEYS,
    CLAUDE_OUTPUT_TERMS,
    CLIENT_PERMISSION_DEFAULTS,
    TextClientBase,
    TOOL_OUTPUT_PREFIX,
    PromptInputSession,
    client_slash_commands,
    client_slash_help_rows,
    collect_token_usage,
    command_text,
    config_value_text,
    configure_readline,
    display_value,
    parse_bool,
    parse_config_bool,
    parse_config_value,
    prefixed_output_labels,
    slash_command_compat_note,
)


CLIENT_VERSION = "prototype"
DEFAULT_TIMEOUT_SECONDS = 900.0
DEFAULT_PERMISSION_MODE = CLIENT_PERMISSION_DEFAULTS.claude_permission_mode
CLAUDE_SKIP_PERMISSIONS_FLAG = CLIENT_PERMISSION_DEFAULTS.claude_skip_permissions_flag
EFFORTS = {"low", "medium", "high", "xhigh", "max"}
DEFAULT_CLAUDE_MODEL = "haiku"
DEFAULT_CLAUDE_EFFORT = "medium"
CLAUDE_MODEL_CHOICES = ("haiku", "sonnet", "opus", "fable", "claude-haiku-4-5", "claude-sonnet-4-6", "claude-opus-4-8")
CLAUDE_MODEL_OPTION_DESCRIPTIONS = {
    "haiku": "Haiku 4.5 - fastest for quick answers",
    "sonnet": "Sonnet 4.6 - efficient for routine tasks",
    "opus": "Opus 4.8 - best for complex tasks",
    "fable": "Fable - unavailable in most accounts",
    "claude-haiku-4-5": "Haiku 4.5 explicit model family",
    "claude-sonnet-4-6": "Sonnet 4.6 explicit model family",
    "claude-opus-4-8": "Opus 4.8 explicit model family",
}
PERMISSION_MODES = {"acceptEdits", "auto", "bypassPermissions", "default", "dontAsk", "plan"}
KNOWN_CONFIG_KEYS = {
    CLAUDE_CONFIG_KEYS.effort,
    CLAUDE_CONFIG_KEYS.model,
    CLAUDE_CONFIG_KEYS.permission,
    CLAUDE_CONFIG_KEYS.raw_output,
    CLAUDE_CONFIG_KEYS.session,
    CLAUDE_CONFIG_KEYS.metrics,
    "text_client.show_status",
    CLAUDE_CONFIG_KEYS.hidden_work_visibility,
    CLAUDE_CONFIG_KEYS.tool_output,
    CLAUDE_CONFIG_KEYS.timeout,
}
UNIMPLEMENTED_CLAUDE_COMMANDS = {
    "agents": "background agent management is not implemented in this stdout prototype",
    "compact": "manual compaction is not implemented in this subprocess prototype",
    "goal": "goal management is not implemented in this stdout prototype",
    "heapdump": "heap dump capture is not implemented in this stdout prototype",
    "init": "project initialization is not implemented here; run real Claude for /init",
    "insights": "insights reporting is not implemented in this stdout prototype",
    "reload-skills": "skill reloading is not implemented here; start a new claude.py turn or run real Claude",
    "review": "review mode is not implemented here; run real Claude for /review",
    "security-review": "security review mode is not implemented here; run real Claude for /security-review",
    "team-onboarding": "team onboarding is not implemented in this stdout prototype",
}
REPL_COMMANDS = client_slash_commands("claude", UNIMPLEMENTED_CLAUDE_COMMANDS)


def split_csv_values(items: list[str]) -> list[str]:
    values: list[str] = []
    for item in items:
        values.extend(part.strip() for part in item.split(",") if part.strip())
    return values


def claude_env() -> dict[str, str]:
    env = dict(os.environ)
    env["TERM"] = "xterm-256color"
    env["NO_COLOR"] = "1"
    return env


class ClaudeTextClient(TextClientBase):
    def __init__(self, args: argparse.Namespace):
        super().__init__(str(args.cwd), prefixed_output_labels(CLAUDE_OUTPUT_TERMS))
        self.args = args
        self.session_id = str(args.resume or args.session_id or "").strip()
        self.current_process: subprocess.Popen[str] | None = None
        self.exit_hint_printed = False
        self.blocks: dict[int, dict[str, Any]] = {}
        self.tool_inputs_printed: set[str] = set()
        self.last_result = ""
        self.last_error = ""
        self.last_cost_usd: float | None = None
        self.last_usage: dict[str, Any] = {}
        self.last_model_usage: dict[str, Any] = {}
        self.init_model = ""
        self.init_permission_mode = ""
        self.init_tools: list[str] = []
        self.init_slash_commands: list[str] = []
        self.stream_normalizer = ClaudeStreamJsonNormalizer()
        self.last_normalized_events: list[dict[str, Any]] = []

    def session_file_path(self) -> str:
        if not self.session_id:
            return "<not started>"
        slug = re.sub(r"[^a-zA-Z0-9]", "-", self.cwd)
        session_path = Path.home() / ".claude" / "projects" / slug / f"{self.session_id}.jsonl"
        return str(session_path) if session_path.exists() else f"<not found: {session_path}>"

    def print_exit_hint(self) -> None:
        if self.exit_hint_printed or not self.session_id:
            return
        self.exit_hint_printed = True
        self.print_aux_stderr(f"[session] {self.session_id}")
        self.print_aux_stderr(f"[resume] {self.resume_command()}")

    def resume_command(self) -> str:
        command = ["python3", str(Path(__file__).resolve())]
        if self.args.model:
            command.extend(["--model", self.args.model])
        if self.args.effort:
            command.extend(["--effort", self.args.effort])
        command.extend(["-C", str(Path(self.args.cwd).expanduser().resolve())])
        for add_dir in self.args.add_dir:
            command.extend(["--add-dir", str(Path(add_dir).expanduser().resolve())])
        for value in self.args.allowed_tools:
            command.extend(["--allowedTools", value])
        for value in self.args.disallowed_tools:
            command.extend(["--disallowedTools", value])
        for value in self.args.tools:
            command.extend(["--tools", value])
        if self.args.permission_mode == DEFAULT_PERMISSION_MODE:
            command.append(CLAUDE_SKIP_PERMISSIONS_FLAG)
        elif self.args.permission_mode:
            command.extend(["--permission-mode", self.args.permission_mode])
        if self.args.system_prompt:
            command.extend(["--system-prompt", self.args.system_prompt])
        if self.args.append_system_prompt:
            command.extend(["--append-system-prompt", self.args.append_system_prompt])
        if self.args.max_budget_usd:
            command.extend(["--max-budget-usd", self.args.max_budget_usd])
        if self.args.show_status:
            command.append("--show-status")
        if not self.args.show_tool_output:
            command.append("--hide-tool-output")
        if self.args.show_thinking:
            command.append("--show-thinking")
        if self.args.raw_json:
            command.append("--raw-json")
        if self.args.show_metrics:
            command.append("--show-metrics")
        if self.args.timeout != DEFAULT_TIMEOUT_SECONDS:
            command.extend(["--timeout", str(self.args.timeout)])
        command.extend(["--resume", self.session_id])
        return command_text(command)

    def prompt_text(self) -> str:
        model = self.effective_model()
        effort = self.args.effort or DEFAULT_CLAUDE_EFFORT
        return self.prompt_text_for(model, effort)

    def effective_model(self) -> str:
        return self.init_model or self.args.model or DEFAULT_CLAUDE_MODEL

    def command_for_turn(self, text: str) -> list[str]:
        claude_path = shutil.which("claude")
        if not claude_path:
            raise RuntimeError("claude CLI not found on PATH")
        command = [
            claude_path,
            "-p",
            "--verbose",
            "--output-format",
            "stream-json",
            "--include-partial-messages",
        ]
        if self.args.model:
            command.extend(["--model", self.args.model])
        if self.args.effort:
            command.extend(["--effort", self.args.effort])
        if self.args.permission_mode == DEFAULT_PERMISSION_MODE:
            command.append(CLAUDE_SKIP_PERMISSIONS_FLAG)
        elif self.args.permission_mode:
            command.extend(["--permission-mode", self.args.permission_mode])
        if self.session_id:
            command.extend(["--resume", self.session_id])
        elif self.args.continue_last:
            command.append("--continue")
        elif self.args.session_id:
            command.extend(["--session-id", self.args.session_id])
        if self.args.add_dir:
            command.append("--add-dir")
            command.extend(str(Path(path).expanduser().resolve()) for path in self.args.add_dir)
        if self.args.allowed_tools:
            command.extend(["--allowedTools", ",".join(split_csv_values(self.args.allowed_tools))])
        if self.args.disallowed_tools:
            command.extend(["--disallowedTools", ",".join(split_csv_values(self.args.disallowed_tools))])
        if self.args.tools:
            command.extend(["--tools", ",".join(split_csv_values(self.args.tools))])
        if self.args.system_prompt:
            command.extend(["--system-prompt", self.args.system_prompt])
        if self.args.append_system_prompt:
            command.extend(["--append-system-prompt", self.args.append_system_prompt])
        if self.args.max_budget_usd:
            command.extend(["--max-budget-usd", self.args.max_budget_usd])
        command.extend(["--", text])
        return command

    def send_turn(self, text: str) -> None:
        if not text.strip():
            return
        self.start_metrics(text)
        self.reset_answer_output_state()
        self.prefixed_output_at_line_start = {label: True for label in prefixed_output_labels(CLAUDE_OUTPUT_TERMS)}
        self.blocks = {}
        self.tool_inputs_printed = set()
        self.last_result = ""
        self.last_error = ""
        self.last_cost_usd = None
        self.last_usage = {}
        self.last_model_usage = {}
        command = self.command_for_turn(text)
        if self.current_metrics is not None:
            self.current_metrics.turn_start_request_at = time.monotonic()
        process = subprocess.Popen(
            command,
            cwd=str(Path(self.args.cwd).expanduser().resolve()),
            env=claude_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            start_new_session=True,
        )
        if self.current_metrics is not None:
            self.current_metrics.turn_start_response_at = time.monotonic()
        self.current_process = process
        try:
            self.read_stream(process)
            stderr_text = process.stderr.read() if process.stderr is not None else ""
            return_code = process.wait(timeout=1)
            self.finish_metrics("complete", self.args.show_metrics)
        except TimeoutError as exc:
            self.interrupt_turn()
            stderr_text = process.stderr.read() if process.stderr is not None else ""
            return_code = process.poll()
            self.finish_metrics("timeout", self.args.show_metrics)
            self.print_aux_stderr(str(exc))
        except KeyboardInterrupt:
            self.interrupt_turn()
            stderr_text = process.stderr.read() if process.stderr is not None else ""
            return_code = process.poll()
            self.finish_metrics("interrupted", self.args.show_metrics)
            self.finish_prefixed_output()
            self.finish_answer_output()
            self.print_aux_stderr("interrupted current turn; returned to prompt")
        finally:
            self.current_process = None
        if stderr_text.strip():
            self.write_prefixed_stdout(TOOL_OUTPUT_PREFIX, stderr_text if stderr_text.endswith("\n") else stderr_text + "\n")
        if return_code not in {0, None}:
            self.print_aux_stderr(f"claude exited with status {return_code}")

    def interrupt_turn(self) -> None:
        process = self.current_process
        if process is None or process.poll() is not None:
            return
        os.killpg(process.pid, signal.SIGINT)
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)

    def read_stream(self, process: subprocess.Popen[str]) -> None:
        if process.stdout is None:
            raise RuntimeError("claude stdout is not available")
        deadline = time.monotonic() + self.args.timeout
        for line in process.stdout:
            if time.monotonic() > deadline:
                raise TimeoutError("timed out waiting for claude")
            if self.args.raw_json:
                self.print_aux_stderr(line.rstrip("\n"))
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError as exc:
                self.write_prefixed_line(TOOL_OUTPUT_PREFIX, f"invalid JSON from claude: {exc}")
                continue
            if isinstance(message, dict):
                self.handle_message(message)
                deadline = time.monotonic() + self.args.timeout
        process.wait(timeout=1)

    def handle_message(self, message: dict[str, Any]) -> None:
        now = time.monotonic()
        message_type = str(message.get("type") or "")
        self.last_normalized_events = self.normalized_stream_events(message)
        metrics = self.current_metrics
        if metrics is not None:
            metrics.mark_server_message(message_type, now)
            collect_token_usage(message, metrics)
        session_id = str(message.get("session_id") or "").strip()
        if session_id and session_id != self.session_id:
            self.session_id = session_id
            self.print_aux_stderr(f"[session] {self.session_id}")
        if message_type == "system":
            self.handle_system_message(message)
            return
        if message_type == "stream_event":
            event = message.get("event") if isinstance(message.get("event"), dict) else {}
            self.handle_stream_event(event)
            return
        if message_type == "assistant":
            self.handle_assistant_message(message)
            return
        if message_type == "user":
            self.handle_user_message(message)
            return
        if message_type == "result":
            self.handle_result_message(message)

    def normalized_stream_events(self, message: dict[str, Any]) -> list[dict[str, Any]]:
        if str(message.get("type") or "") == "stream_event":
            event = message.get("event") if isinstance(message.get("event"), dict) else {}
            return self.stream_normalizer.normalize_item(event)
        return self.stream_normalizer.normalize_item(message)

    def handle_system_message(self, message: dict[str, Any]) -> None:
        subtype = str(message.get("subtype") or "")
        if subtype == "init":
            raw = str(message.get("model") or "").strip()
            self.init_model = re.sub(r"\x1b?\[\d+m\]?$", "", raw).strip()
            self.init_permission_mode = str(message.get("permissionMode") or "").strip()
            tools = message.get("tools")
            self.init_tools = [str(tool) for tool in tools] if isinstance(tools, list) else []
            slash_commands = message.get("slash_commands")
            self.init_slash_commands = [str(command) for command in slash_commands] if isinstance(slash_commands, list) else []
            return
        if subtype == "status" and self.args.show_status:
            status = str(message.get("status") or "").strip()
            if status:
                self.write_prefixed_line(TOOL_OUTPUT_PREFIX, f"status: {status}")

    def handle_stream_event(self, event: dict[str, Any]) -> None:
        now = time.monotonic()
        metrics = self.current_metrics
        event_type = str(event.get("type") or "")
        if metrics is not None:
            metrics.mark_server_message(event_type, now)
        if event_type == "content_block_start":
            index = int(event.get("index") or 0)
            block = event.get("content_block") if isinstance(event.get("content_block"), dict) else {}
            block_type = str(block.get("type") or "")
            self.blocks[index] = {"type": block_type, "json_parts": [], "tool": block}
            if block_type == "tool_use" and metrics is not None:
                metrics.record_tool_item("start", {"type": "tool_use"}, now)
            if block_type == "tool_use" and self.args.show_tool_output:
                name = str(block.get("name") or "tool")
                self.write_prefixed_line(TOOL_OUTPUT_PREFIX, f"start {name}")
            return
        if event_type == "content_block_delta":
            index = int(event.get("index") or 0)
            delta = event.get("delta") if isinstance(event.get("delta"), dict) else {}
            delta_type = str(delta.get("type") or "")
            if delta_type == "text_delta":
                text = str(delta.get("text") or "")
                if text:
                    if metrics is not None:
                        metrics.record_answer_text(text, now)
                    self.finish_prefixed_output()
                    self.write_answer_stdout(text)
                return
            if delta_type == "input_json_delta":
                partial = str(delta.get("partial_json") or "")
                block = self.blocks.setdefault(index, {"type": "tool_use", "json_parts": [], "tool": {}})
                block["json_parts"].append(partial)
                return
            if delta_type in {"thinking_delta", "redacted_thinking_delta"} and self.args.show_thinking:
                thinking = str(delta.get("thinking") or delta.get("text") or "")
                if thinking:
                    if metrics is not None:
                        metrics.record_raw_reasoning(thinking, now)
                    self.write_prefixed_stdout(CLAUDE_OUTPUT_TERMS.prefix, thinking)
                return
        if event_type == "content_block_stop":
            index = int(event.get("index") or 0)
            block = self.blocks.get(index)
            if block and block.get("type") == "tool_use" and metrics is not None:
                metrics.record_tool_item("done", {"type": "tool_use"}, now)
            self.emit_tool_input_for_block(index)
            return

    def handle_assistant_message(self, message: dict[str, Any]) -> None:
        payload = message.get("message") if isinstance(message.get("message"), dict) else {}
        model = str(payload.get("model") or "").strip()
        if model:
            self.init_model = model
        content = payload.get("content") if isinstance(payload.get("content"), list) else []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "tool_use":
                self.emit_tool_use(item)

    def handle_user_message(self, message: dict[str, Any]) -> None:
        metrics = self.current_metrics
        now = time.monotonic()
        result = message.get("tool_use_result") if isinstance(message.get("tool_use_result"), dict) else {}
        if result and metrics is not None:
            metrics.record_tool_item("done", {"type": "tool_use"}, now)
            metrics.record_tool_output(str(result.get("stdout") or "") + str(result.get("stderr") or ""), now)
        if not self.args.show_tool_output:
            return
        if not result:
            return
        stdout = str(result.get("stdout") or "")
        stderr = str(result.get("stderr") or "")
        interrupted = bool(result.get("interrupted"))
        self.write_prefixed_line(TOOL_OUTPUT_PREFIX, "result" + (" interrupted" if interrupted else ""))
        if stdout:
            self.write_prefixed_line(TOOL_OUTPUT_PREFIX, "stdout:")
            self.write_prefixed_stdout(TOOL_OUTPUT_PREFIX, stdout if stdout.endswith("\n") else stdout + "\n")
        if stderr:
            self.write_prefixed_line(TOOL_OUTPUT_PREFIX, "stderr:")
            self.write_prefixed_stdout(TOOL_OUTPUT_PREFIX, stderr if stderr.endswith("\n") else stderr + "\n")

    def handle_result_message(self, message: dict[str, Any]) -> None:
        metrics = self.current_metrics
        now = time.monotonic()
        self.session_id = str(message.get("session_id") or self.session_id).strip()
        self.last_result = str(message.get("result") or "")
        if metrics is not None:
            collect_token_usage(message, metrics)
            if self.last_result and metrics.answer_chars == 0:
                metrics.record_answer_text(self.last_result, now)
        self.last_error = str(message.get("api_error_status") or "")
        cost = message.get("total_cost_usd")
        self.last_cost_usd = float(cost) if isinstance(cost, int | float) else None
        usage = message.get("usage")
        self.last_usage = usage if isinstance(usage, dict) else {}
        model_usage = message.get("modelUsage")
        self.last_model_usage = model_usage if isinstance(model_usage, dict) else {}
        self.finish_prefixed_output()
        self.finish_answer_output()
        if message.get("is_error"):
            error_text = self.last_error or str(message.get("subtype") or "error")
            self.print_aux_stderr(f"claude error: {error_text}")

    def emit_tool_input_for_block(self, index: int) -> None:
        block = self.blocks.get(index)
        if not block or block.get("type") != "tool_use":
            return
        tool = block.get("tool") if isinstance(block.get("tool"), dict) else {}
        if str(tool.get("id") or "") in self.tool_inputs_printed:
            return
        if block.get("json_parts"):
            tool = dict(tool)
            raw_json = "".join(str(part) for part in block["json_parts"])
            try:
                tool["input"] = json.loads(raw_json) if raw_json else {}
            except json.JSONDecodeError:
                tool["input"] = raw_json
        self.emit_tool_use(tool)

    def emit_tool_use(self, item: dict[str, Any]) -> None:
        if not self.args.show_tool_output:
            return
        tool_id = str(item.get("id") or "").strip()
        if tool_id and tool_id in self.tool_inputs_printed:
            return
        if tool_id:
            self.tool_inputs_printed.add(tool_id)
        name = str(item.get("name") or "tool")
        tool_input = item.get("input")
        summary = self.tool_input_summary(name, tool_input)
        if summary:
            self.write_prefixed_highlighted_line(TOOL_OUTPUT_PREFIX, f"call {name}", summary)
        else:
            self.write_prefixed_line(TOOL_OUTPUT_PREFIX, f"call {name}")

    def tool_input_summary(self, name: str, tool_input: Any) -> str:
        if isinstance(tool_input, dict):
            if name == "Bash":
                command = str(tool_input.get("command") or "").strip()
                description = str(tool_input.get("description") or "").strip()
                return command or description
            for key in ("file_path", "path", "pattern", "url", "prompt"):
                value = str(tool_input.get(key) or "").strip()
                if value:
                    return value
            return json.dumps(tool_input, sort_keys=True)
        if isinstance(tool_input, str):
            return tool_input
        return ""

    def handle_repl_command(self, text: str) -> str:
        command, _, rest = text[1:].strip().partition(" ")
        command = command.strip().lower().replace("_", "-")
        if command in {"q", "quit", "exit"}:
            return "quit"
        if command == "help":
            self.print_repl_help()
            return "handled"
        if command == "status":
            self.print_status()
            return "handled"
        if command == "clear":
            self.clear_conversation()
            return "handled"
        if command == "cls":
            print("\033c", end="")
            return "handled"
        if command == "context":
            self.print_context()
            return "handled"
        if command == "usage":
            self.print_usage()
            return "handled"
        if command == "metrics":
            self.handle_metrics_command(rest)
            return "handled"
        if command == "model":
            self.handle_model_command(rest)
            return "handled"
        if command == "effort":
            self.handle_effort_command(rest)
            return "handled"
        if command in {"permission", "permission-mode", "permissions"}:
            self.handle_permission_mode_command(rest)
            return "handled"
        if command in {"thinking", "reasoning"}:
            self.handle_thinking_command(rest, command)
            return "handled"
        if command == "resume":
            self.handle_resume_command(rest)
            return "handled"
        if command == "config":
            self.handle_config_command(rest)
            return "handled"
        if command == "raw":
            self.handle_raw_command(rest)
            return "handled"
        if command in UNIMPLEMENTED_CLAUDE_COMMANDS:
            print(f"/{command}: {UNIMPLEMENTED_CLAUDE_COMMANDS[command]}")
            return "handled"
        print(f"unknown command: /{command}")
        print("run /help for available commands")
        return "handled"

    def print_repl_help(self) -> None:
        print("Slash commands:")
        for row in client_slash_help_rows("claude"):
            print(row)
        print("Recognized but not implemented in this text prototype:")
        print("  " + " ".join(f"/{name}" for name in sorted(UNIMPLEMENTED_CLAUDE_COMMANDS)))
        print("Common settings:")
        print("  /config model=sonnet")
        print("  /config effort=high")
        print(f"  /config {CLAUDE_CONFIG_KEYS.permission}={DEFAULT_PERMISSION_MODE}")
        print(f"  /config {CLAUDE_CONFIG_KEYS.tool_output}=false")
        print(f"  /config {CLAUDE_CONFIG_KEYS.metrics}=true")
        print(f"  /config {CLAUDE_CONFIG_KEYS.hidden_work_visibility}=true")
        print("Keyboard shortcuts:")
        print("  Ctrl-A start, Ctrl-E end, Ctrl-K kill to end, Ctrl-U kill line")
        print("  Ctrl-W kill word, Ctrl-Y yank, Ctrl-P/N history, Ctrl-R history search")
        print("  Alt-B/F move word, Alt-D kill word, Tab completes slash commands")

    def print_status(self) -> None:
        print("Claude Code")
        print(f"  Model:              {self.effective_model()}" + (f" (configured {self.args.model})" if self.init_model and self.args.model and self.init_model != self.args.model else ""))
        print(f"  Effort:             {display_value(self.args.effort)}")
        print(f"  Directory:          {self.display_cwd()}")
        print(f"  Permission mode:    {self.args.permission_mode}" + (f" ({self.init_permission_mode})" if self.init_permission_mode else ""))
        print(f"  Session:            {self.session_id or '<not started>'}")
        print(f"  Tool output:        {'on' if self.args.show_tool_output else 'off'}")
        print(f"  {CLAUDE_OUTPUT_TERMS.output_label}: {'on' if self.args.show_thinking else 'off'}")
        print(f"  Metrics:            {'on' if self.args.show_metrics else 'off'}")
        print(f"  Raw JSON:           {'on' if self.args.raw_json else 'off'}")
        print(f"  Terminal bg:        {self.terminal_background_text()}")
        if self.last_cost_usd is not None:
            print(f"  Last cost USD:      {self.last_cost_usd:.6f}")
        if self.init_tools:
            print(f"  Tools:              {len(self.init_tools)} available")

    def print_context(self) -> None:
        print("Claude Context")
        print(f"  Directory:          {self.display_cwd()}")
        print(f"  Session:            {self.session_id or '<not started>'}")
        print(f"  Model:              {self.effective_model()}" + (f" (configured {self.args.model})" if self.init_model and self.args.model and self.init_model != self.args.model else ""))
        print(f"  Effort:             {display_value(self.args.effort)}")
        print(f"  Permission mode:    {self.args.permission_mode}" + (f" ({self.init_permission_mode})" if self.init_permission_mode else ""))
        print(f"  Add dirs:           {', '.join(self.args.add_dir) if self.args.add_dir else '<none>'}")
        print(f"  Allowed tools:      {', '.join(split_csv_values(self.args.allowed_tools)) if self.args.allowed_tools else '<default>'}")
        print(f"  Disallowed tools:   {', '.join(split_csv_values(self.args.disallowed_tools)) if self.args.disallowed_tools else '<none>'}")
        print(f"  Tools override:     {', '.join(split_csv_values(self.args.tools)) if self.args.tools else '<default>'}")
        if self.init_tools:
            print(f"  Available tools:    {', '.join(self.init_tools)}")
        if self.init_slash_commands:
            builtins = [command for command in self.init_slash_commands if command in REPL_COMMANDS or command in UNIMPLEMENTED_CLAUDE_COMMANDS]
            print(f"  Claude commands:    {', '.join(builtins) if builtins else '<reported after first turn>'}")

    def clear_conversation(self) -> None:
        self.session_id = ""
        self.args.resume = ""
        self.args.continue_last = False
        self.args.session_id = ""
        self.last_result = ""
        self.last_error = ""
        self.last_cost_usd = None
        self.last_usage = {}
        self.last_model_usage = {}
        self.last_metrics = None
        print("Conversation cleared; next turn starts a new Claude session.")

    def print_usage(self) -> None:
        print("Claude Usage")
        if self.last_cost_usd is None and not self.last_usage and not self.last_model_usage:
            print("  No completed turn yet.")
            return
        if self.last_cost_usd is not None:
            print(f"  Last cost USD:      {self.last_cost_usd:.6f}")
        for key in ["input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens"]:
            if key in self.last_usage:
                print(f"  {key}: {self.last_usage[key]}")
        server_tool_use = self.last_usage.get("server_tool_use") if isinstance(self.last_usage.get("server_tool_use"), dict) else {}
        if server_tool_use:
            print(f"  Server tools:       {json.dumps(server_tool_use, sort_keys=True)}")
        if self.last_model_usage:
            print("  Models:")
            for model, usage in self.last_model_usage.items():
                if isinstance(usage, dict):
                    cost = usage.get("costUSD")
                    input_tokens = usage.get("inputTokens")
                    output_tokens = usage.get("outputTokens")
                    print(f"    {model}: input={input_tokens}, output={output_tokens}, cost={cost}")
        if self.last_metrics is not None:
            print("  Metrics:")
            for line in self.metrics_lines(self.last_metrics):
                print(f"    {line}")

    def handle_metrics_command(self, rest: str) -> None:
        value = rest.strip().lower()
        if not value:
            print(f"Metrics {'on' if self.args.show_metrics else 'off'}")
            self.print_metrics()
            return
        if value in {"last", "show"}:
            self.print_metrics()
            return
        try:
            self.args.show_metrics = parse_bool(value)
        except ValueError as exc:
            print(str(exc))
            print("Usage: /metrics [on|off|last]")
            return
        print(f"Metrics {'on' if self.args.show_metrics else 'off'}")

    def handle_model_command(self, rest: str) -> None:
        parts = shlex.split(rest)
        if not parts:
            print("Select model")
            print(f"  Current:    {self.effective_model()}")
            if self.init_model and self.args.model and self.init_model != self.args.model:
                print(f"  Configured: {self.args.model}")
            print("  Available:")
            for model in CLAUDE_MODEL_CHOICES:
                marker = "*" if model == self.args.model else " "
                description = CLAUDE_MODEL_OPTION_DESCRIPTIONS.get(model, "")
                print(f"   {marker} {model:<20} {description}")
            print("Usage: /model <name>")
            return
        self.print_compat_note("model", "Claude", "Implemented here to match Codex-style runtime model switching.")
        self.args.model = DEFAULT_CLAUDE_MODEL if parts[0].strip().lower() in {"default", "recommended"} else parts[0]
        self.init_model = ""
        print(f"model changed: {self.args.model}")

    def handle_effort_command(self, rest: str) -> None:
        parts = shlex.split(rest)
        if not parts:
            print(f"Effort: {display_value(self.args.effort)}")
            print(f"Usage: /effort [{'|'.join(sorted(EFFORTS))}]")
            return
        effort = parts[0].strip().lower()
        if effort not in EFFORTS:
            print(f"effort must be one of: {', '.join(sorted(EFFORTS))}")
            return
        self.args.effort = effort
        print(f"effort changed: {effort}")

    def handle_permission_mode_command(self, rest: str) -> None:
        self.print_compat_note("permission-mode", "Claude", "Implemented here as a runtime wrapper around Claude's --permission-mode flag.")
        parts = shlex.split(rest)
        if not parts:
            print(f"Permission mode: {self.args.permission_mode}")
            print(f"Usage: /permission-mode [{'|'.join(sorted(PERMISSION_MODES))}]")
            return
        mode = parts[0].strip()
        if mode not in PERMISSION_MODES:
            print(f"permission mode must be one of: {', '.join(sorted(PERMISSION_MODES))}")
            return
        self.args.permission_mode = mode
        print(f"permission mode changed: {mode}")

    def handle_thinking_command(self, rest: str, command: str = "thinking") -> None:
        note = slash_command_compat_note("claude", command)
        if note:
            self.print_compat_note(command, "Claude", note)
        value = rest.strip().lower()
        try:
            self.args.show_thinking = not self.args.show_thinking if not value else parse_bool(value)
        except ValueError as exc:
            print(str(exc))
            return
        print(f"{CLAUDE_OUTPUT_TERMS.output_label} {'on' if self.args.show_thinking else 'off'}")

    def handle_resume_command(self, rest: str) -> None:
        session_id = rest.strip()
        if not session_id:
            print("Usage: /resume <session-id>")
            return
        self.session_id = session_id
        self.args.resume = session_id
        self.args.continue_last = False
        print(f"Session: {self.session_id}")

    def handle_raw_command(self, rest: str) -> None:
        self.print_compat_note("raw", "Claude", "Implemented by this text client to toggle clone-specific stream-json diagnostics.")
        value = rest.strip().lower()
        try:
            self.args.raw_json = not self.args.raw_json if not value else parse_bool(value)
        except ValueError as exc:
            print(str(exc))
            return
        print(f"Raw JSON {'on' if self.args.raw_json else 'off'}")

    def handle_config_command(self, rest: str) -> None:
        parts = shlex.split(rest)
        if not parts:
            self.print_config_settings()
            print("Usage: /config key=value [key=value ...]")
            return
        for item in parts:
            key, separator, raw_value = item.partition("=")
            if not separator:
                print(f"invalid config override {item!r}; expected key=value")
                return
            if key not in KNOWN_CONFIG_KEYS:
                print(f"unrecognized config key: {key}")
                return
            value = parse_config_value(raw_value)
            try:
                normalized = self.apply_config_override(key, value)
            except ValueError as exc:
                print(str(exc))
                return
            print(f"{key} = {config_value_text(normalized)}")

    def print_config_settings(self) -> None:
        rows = {
            CLAUDE_CONFIG_KEYS.model: self.args.model,
            CLAUDE_CONFIG_KEYS.effort: self.args.effort,
            CLAUDE_CONFIG_KEYS.permission: self.args.permission_mode,
            CLAUDE_CONFIG_KEYS.metrics: self.args.show_metrics,
            CLAUDE_CONFIG_KEYS.tool_output: self.args.show_tool_output,
            CLAUDE_CONFIG_KEYS.hidden_work_visibility: self.args.show_thinking,
            "text_client.show_status": self.args.show_status,
            CLAUDE_CONFIG_KEYS.raw_output: self.args.raw_json,
            CLAUDE_CONFIG_KEYS.timeout: self.args.timeout,
            CLAUDE_CONFIG_KEYS.session: self.session_id,
        }
        for key, value in rows.items():
            print(f"{key} = {config_value_text(value)}")

    def apply_config_override(self, key: str, value: Any) -> Any:
        if key == CLAUDE_CONFIG_KEYS.model:
            self.args.model = str(value)
            return self.args.model
        if key == CLAUDE_CONFIG_KEYS.effort:
            effort = str(value).strip().lower()
            if effort not in EFFORTS:
                raise ValueError(f"{CLAUDE_CONFIG_KEYS.effort} must be one of: {', '.join(sorted(EFFORTS))}")
            self.args.effort = effort
            return effort
        if key == CLAUDE_CONFIG_KEYS.permission:
            mode = str(value)
            if mode not in PERMISSION_MODES:
                raise ValueError(f"{CLAUDE_CONFIG_KEYS.permission} must be one of: {', '.join(sorted(PERMISSION_MODES))}")
            self.args.permission_mode = mode
            return mode
        if key == CLAUDE_CONFIG_KEYS.tool_output:
            self.args.show_tool_output = parse_config_bool(value)
            return self.args.show_tool_output
        if key == CLAUDE_CONFIG_KEYS.metrics:
            self.args.show_metrics = parse_config_bool(value)
            return self.args.show_metrics
        if key == CLAUDE_CONFIG_KEYS.hidden_work_visibility:
            self.args.show_thinking = parse_config_bool(value)
            return self.args.show_thinking
        if key == "text_client.show_status":
            self.args.show_status = parse_config_bool(value)
            return self.args.show_status
        if key == CLAUDE_CONFIG_KEYS.raw_output:
            self.args.raw_json = parse_config_bool(value)
            return self.args.raw_json
        if key == CLAUDE_CONFIG_KEYS.timeout:
            self.args.timeout = float(value)
            return self.args.timeout
        if key == CLAUDE_CONFIG_KEYS.session:
            self.session_id = str(value)
            self.args.resume = self.session_id
            self.args.continue_last = False
            return self.session_id
        raise ValueError(f"unrecognized config key: {key}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Claude Code text client prototype using Claude stream-json output.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("prompt", nargs="*", help="Optional user prompt to start the session.")
    parser.add_argument("-m", "--model", default=DEFAULT_CLAUDE_MODEL, metavar="MODEL", help=f"Model or alias. Available in this client: {', '.join(CLAUDE_MODEL_CHOICES)}.")
    parser.add_argument("--effort", choices=sorted(EFFORTS), default=DEFAULT_CLAUDE_EFFORT, metavar="LEVEL", help="Effort for the current session.")
    parser.add_argument("-C", "--cd", dest="cwd", default=os.getcwd(), metavar="DIR", help="Client convenience: run Claude with this working directory.")
    parser.add_argument("--add-dir", action="append", default=[], metavar="DIR", help="Additional directories to allow tool access to.")
    parser.add_argument("--allowedTools", "--allowed-tools", dest="allowed_tools", action="append", default=[], metavar="TOOLS", help="Comma-separated Claude tools to allow.")
    parser.add_argument("--disallowedTools", "--disallowed-tools", dest="disallowed_tools", action="append", default=[], metavar="TOOLS", help="Comma-separated Claude tools to deny.")
    parser.add_argument("--tools", action="append", default=[], metavar="TOOLS", help="Comma-separated built-in tools, or an empty string to disable all tools.")
    parser.add_argument("--permission-mode", choices=sorted(PERMISSION_MODES), default=DEFAULT_PERMISSION_MODE, help=f"Claude permission mode. Default: {DEFAULT_PERMISSION_MODE}.")
    parser.add_argument(CLAUDE_SKIP_PERMISSIONS_FLAG, action="store_true", help=f"Claude-compatible alias for --permission-mode {DEFAULT_PERMISSION_MODE}.")
    parser.add_argument("-r", "--resume", default="", metavar="SESSION_ID", help="Resume a Claude session id.")
    parser.add_argument("-c", "--continue", dest="continue_last", action="store_true", help="Continue the most recent conversation in this directory.")
    parser.add_argument("--session-id", default="", metavar="UUID", help="Use a specific session id for a new conversation.")
    parser.add_argument("--system-prompt", default="", metavar="PROMPT", help="System prompt for the session.")
    parser.add_argument("--append-system-prompt", default="", metavar="PROMPT", help="Append to Claude's default system prompt.")
    parser.add_argument("--max-budget-usd", default="", metavar="USD", help="Maximum dollar amount to spend for a print-mode turn.")
    parser.add_argument("--show-status", action="store_true", help="Show Claude status events as tool lines.")
    parser.add_argument("--hide-tool-output", dest="show_tool_output", action="store_false", help="Hide gray tool lines.")
    parser.add_argument("--show-thinking", dest="show_thinking", action="store_true", help=f"Show gray {CLAUDE_OUTPUT_TERMS.lower_label} lines when Claude emits them.")
    parser.add_argument("--hide-thinking", dest="show_thinking", action="store_false", help=f"Hide gray {CLAUDE_OUTPUT_TERMS.lower_label} lines.")
    parser.add_argument("--show-metrics", dest="show_metrics", action="store_true", help="Show TTFT, ISL/OSL, token rate, and tool timing after each turn.")
    parser.add_argument("--hide-metrics", dest="show_metrics", action="store_false", help="Hide turn metrics.")
    parser.add_argument("--raw-json", action="store_true", help="Echo raw stream-json events to stderr.")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS, help="Per-turn timeout in seconds.")
    parser.add_argument("--mock", action="store_true", help="Run the built-in Claude TUI mock and prompt-corpus fixture replay.")
    parser.add_argument("--dump-fixtures", action="store_true", help="Dump this agent's prompt-corpus fixtures to stdout and exit.")
    parser.add_argument("-V", "--version", action="version", version=f"claude-text-client {CLIENT_VERSION}")
    parser.set_defaults(show_tool_output=True, show_thinking=True, show_metrics=False)
    args = parser.parse_args()
    if args.dangerously_skip_permissions:
        args.permission_mode = DEFAULT_PERMISSION_MODE
    args.cwd = str(Path(args.cwd).expanduser().resolve())
    return args


def main() -> int:
    args = parse_args()
    mock_agent_common.configure_claude_mock(display_cwd_override=args.cwd)
    if args.dump_fixtures:
        mock_agent_common.print_mock_fixture_dump()
        return 0
    if args.mock:
        mock_agent_common.main()
        return 0
    client = ClaudeTextClient(args)
    use_mock_tui = False
    composer_state: dict[str, str] = {}
    exit_notice = ""
    try:
        initial_prompt = " ".join(args.prompt).strip()
        if initial_prompt:
            client.send_turn(initial_prompt)
            return 0
        use_mock_tui = sys.stdin.isatty() and sys.stdout.isatty()
        if use_mock_tui:
            mock_agent_common.setup_history()
            mock_agent_common.print_startup(composer_state)
        else:
            configure_readline(REPL_COMMANDS)
        prompt_session = PromptInputSession(REPL_COMMANDS)
        if not use_mock_tui:
            print(f"Claude text client. Type /quit to exit.\n  session: {client.session_id or 'new'} jsonl: {client.session_file_path()}", file=sys.stderr)
        while True:
            try:
                if use_mock_tui:
                    text = mock_agent_common.read_live_composer(composer_state)
                    if text.strip():
                        mock_agent_common.print_user_header(text)
                else:
                    text = prompt_session.read(client.prompt_text())
            except EOFError:
                print("", file=sys.stderr)
                return 0
            stripped = text.strip()
            if not stripped:
                continue
            if stripped in {"/q", "/quit", "quit", "exit"}:
                return 0
            if stripped.startswith("/"):
                result = client.handle_repl_command(stripped)
                if result == "quit":
                    return 0
                continue
            client.send_turn(text)
    except KeyboardInterrupt:
        if use_mock_tui:
            exit_notice = "interrupted"
        else:
            client.print_aux_stderr("\ninterrupted")
        return 130
    finally:
        if use_mock_tui:
            exit_lines = [exit_notice] if exit_notice else []
            if client.session_id and not client.exit_hint_printed:
                exit_lines.extend([f"[session] {client.session_id}", f"[resume] {client.resume_command()}"])
            line_count = mock_agent_common.terminal_display_line_count(exit_lines) if exit_lines else 0
            mock_agent_common.prepare_terminal_for_shell(line_count, composer_state)
            if exit_notice:
                print(exit_notice, file=sys.stderr)
        client.print_exit_hint()


if __name__ == "__main__":
    raise SystemExit(main())
