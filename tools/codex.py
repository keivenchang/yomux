#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Text prototype client for `codex app-server`.

This intentionally talks to the structured JSON-RPC app-server instead of scraping a
visible Codex TUI. It is a prototype for the kind of client YOLOmux can own directly:
start/resume a Codex thread, send turns, stream answer deltas, optionally display
Codex reasoning/thinking summaries and raw events when the server emits them, and relay basic
approval prompts.

Usage:
  python3 tools/codex.py
  python3 tools/codex.py -C . "summarize this repo"
  python3 tools/codex.py -m gpt-5.4-mini -c model_reasoning_effort=\"low\"
"""
from __future__ import annotations

import argparse
import json
import os
import selectors
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from yolomux_lib.agent_comms.codex_app_server import CodexAppServerProtocol
from yolomux_lib.agent_comms.json_rpc import json_rpc_error
from yolomux_lib.agent_comms.json_rpc import json_rpc_notification
from yolomux_lib.agent_comms.json_rpc import json_rpc_request
from yolomux_lib.agent_comms.json_rpc import json_rpc_response
from yolomux_lib.agent_comms.stream_events import normalize_codex_app_server_message
from text_client_common import (
    CODEX_CONFIG_KEYS,
    CODEX_OUTPUT_TERMS,
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
APP_SERVER_TIMEOUT_SECONDS = 300.0
HELP_MODEL_QUERY_TIMEOUT_SECONDS = 2.0
DEFAULT_SANDBOX = CLIENT_PERMISSION_DEFAULTS.codex_sandbox
DEFAULT_APPROVAL_POLICY = CLIENT_PERMISSION_DEFAULTS.codex_approval_policy
DEFAULT_BYPASS_HOOK_TRUST = CLIENT_PERMISSION_DEFAULTS.codex_bypass_hook_trust
DEFAULT_TEXT_CLIENT_APPROVAL_MODE = CLIENT_PERMISSION_DEFAULTS.codex_text_client_approval_mode
CODEX_BYPASS_APPROVALS_FLAG = CLIENT_PERMISSION_DEFAULTS.codex_bypass_approvals_flag
CODEX_BYPASS_HOOK_TRUST_FLAG = CLIENT_PERMISSION_DEFAULTS.codex_bypass_hook_trust_flag
CODEX_YOLO_ALIAS = CLIENT_PERMISSION_DEFAULTS.codex_yolo_alias
APPROVAL_METHODS = {
    "item/commandExecution/requestApproval",
    "item/fileChange/requestApproval",
    "applyPatchApproval",
    "execCommandApproval",
}
UNIMPLEMENTED_CODEX_COMMANDS = {
    "app": "app selection is not implemented in this stdout prototype",
    "apps": "app selection is not implemented in this stdout prototype",
    "archive": "session archiving is not implemented in this stdout prototype",
    "compact": "conversation compaction is not implemented in this stdout prototype",
    "copy": "clipboard integration is not implemented in this stdout prototype",
    "delete": "session deletion is not implemented in this stdout prototype",
    "diff": "diff viewing is not implemented in this stdout prototype",
    "experimental": "feature flag editing is not implemented in this stdout prototype",
    "feedback": "feedback upload is not implemented in this stdout prototype",
    "fork": "thread forking is not implemented in this stdout prototype",
    "goal": "goal management is not implemented in this stdout prototype",
    "ide": "IDE context control is not implemented in this stdout prototype",
    "import": "external agent config import is not implemented in this stdout prototype",
    "init": "AGENTS.md generation is not implemented here; run real Codex for /init",
    "keymap": "keymap editing is not implemented in this stdout prototype",
    "mcp": "MCP browser UI is not implemented in this stdout prototype",
    "memories": "memory management is not implemented in this stdout prototype",
    "personality": "personality selection is not implemented in this stdout prototype",
    "plugins": "plugin management is not implemented in this stdout prototype",
    "ps": "background terminal listing is not implemented in this stdout prototype",
    "rename": "thread renaming is not implemented in this stdout prototype",
    "review": "review mode is not implemented here; run real Codex for /review",
    "sandbox-add-read-dir": "adding read directories is not implemented in this stdout prototype",
    "side": "side conversations are not implemented in this stdout prototype",
    "skills": "skill listing is not implemented in this stdout prototype",
    "statusline": "status line configuration is not implemented in this stdout prototype",
    "stop": "background terminal stop is not implemented in this stdout prototype",
}
REPL_COMMANDS = client_slash_commands("codex", UNIMPLEMENTED_CODEX_COMMANDS)
APPROVAL_POLICIES = {"untrusted", "on-failure", "on-request", "never"}
SANDBOX_MODES = {"read-only", "workspace-write", "danger-full-access"}
REASONING_EFFORT_ORDER = ["minimal", "low", "medium", "high", "xhigh"]
REASONING_EFFORTS = set(REASONING_EFFORT_ORDER)
REASONING_SUMMARIES = {"none", "auto", "concise", "detailed"}
REASONING_SUMMARY_ALIASES = {"summary": "concise"}
DEFAULT_CODEX_MODEL = "gpt-5.4-mini"
DEFAULT_CODEX_EFFORT = "medium"
CODEX_MODELS_WITHOUT_REASONING_SUMMARY = {"gpt-5.3-codex-spark"}
KNOWN_CONFIG_KEYS = {
    "approval_policy",
    "bypass_hook_trust",
    CODEX_CONFIG_KEYS.model,
    CODEX_CONFIG_KEYS.effort,
    "model_reasoning_summary",
    "sandbox",
    "sandbox_mode",
    "service_tier",
    "text_client.approval_mode",
    "text_client.include_hidden_models",
    CODEX_CONFIG_KEYS.raw_output,
    "text_client.debug_json",
    CODEX_CONFIG_KEYS.metrics,
    CODEX_CONFIG_KEYS.hidden_work_raw,
    CODEX_CONFIG_KEYS.hidden_work_summary,
    CODEX_CONFIG_KEYS.tool_output,
    CODEX_CONFIG_KEYS.session,
    CODEX_CONFIG_KEYS.timeout,
    "web_search",
}


def initialize_params() -> dict[str, Any]:
    return {
        "clientInfo": {"name": "codex-text-client", "title": "Codex Text Client", "version": CLIENT_VERSION},
        "capabilities": {"experimentalApi": True, "requestAttestation": False},
    }


def shell_command_text(command: Any) -> str:
    if isinstance(command, str):
        return command
    if isinstance(command, list):
        return " ".join(shlex.quote(str(part)) for part in command)
    return ""


def turn_text(turn: Any) -> str:
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
            parts.append(item["text"].strip())
    return "\n".join(part for part in parts if part).strip()


def turn_error_message(turn: Any) -> str:
    if not isinstance(turn, dict):
        return ""
    for key in ("error", "lastError", "failure", "failedReason"):
        message = human_error_message(turn.get(key))
        if message:
            return message
    status = str(turn.get("status") or "").strip()
    if status in {"failed", "cancelled", "canceled"}:
        return f"turn {status}"
    return ""


def app_server_env() -> dict[str, str]:
    env = dict(os.environ)
    local_bin = str(Path.home() / ".local" / "bin")
    path_entries = env.get("PATH", "").split(os.pathsep) if env.get("PATH") else []
    if local_bin not in path_entries:
        env["PATH"] = os.pathsep.join([local_bin, *path_entries]) if path_entries else local_bin
    env["TERM"] = "xterm-256color"
    env["NO_COLOR"] = "1"
    return env


def ordered_reasoning_efforts(values: Iterable[str]) -> list[str]:
    seen = {value for value in values if value}
    ordered = [effort for effort in REASONING_EFFORT_ORDER if effort in seen]
    ordered.extend(sorted(seen - set(REASONING_EFFORT_ORDER)))
    return ordered


def reasoning_efforts_text(values: Iterable[str] = REASONING_EFFORT_ORDER) -> str:
    return ", ".join(ordered_reasoning_efforts(values))


def model_effort_values(row: dict[str, Any]) -> list[str]:
    efforts = row.get("supportedReasoningEfforts")
    if not isinstance(efforts, list):
        return []
    values = []
    for item in efforts:
        if isinstance(item, dict):
            value = str(item.get("reasoningEffort") or item.get("effort") or "").strip()
        else:
            value = str(item).strip()
        if value:
            values.append(value)
    return ordered_reasoning_efforts(values)


def model_catalog_efforts(rows: list[dict[str, Any]]) -> list[str]:
    efforts: list[str] = []
    for row in rows:
        efforts.extend(model_effort_values(row))
    return ordered_reasoning_efforts(efforts)


def model_catalog_row_model(row: dict[str, Any]) -> str:
    return str(row.get("model") or row.get("id") or "").strip()


def model_catalog_row_matches(row: dict[str, Any], model: str) -> bool:
    target = model.strip()
    if not target:
        return False
    folded = target.casefold()
    for key in ("model", "id", "displayName"):
        value = str(row.get(key) or "").strip()
        if value and (value == target or value.casefold() == folded):
            return True
    return False


def normalized_model_id(model: str) -> str:
    return model.strip().casefold()


def codex_model_supports_reasoning_summary(model: str, extra_unsupported_models: set[str] | None = None) -> bool:
    model_id = normalized_model_id(model)
    if not model_id:
        return True
    unsupported = {normalized_model_id(item) for item in CODEX_MODELS_WITHOUT_REASONING_SUMMARY}
    if extra_unsupported_models:
        unsupported.update(normalized_model_id(item) for item in extra_unsupported_models)
    return model_id not in unsupported


def reasoning_summary_unsupported_error(value: Any) -> bool:
    message = human_error_message(value).casefold()
    return "unsupported parameter" in message and "reasoning.summary" in message


def thread_not_found_error(value: Any) -> bool:
    message = human_error_message(value).casefold()
    return "thread not found" in message


def default_model_catalog_row(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    preferred = next((row for row in rows if not row.get("hidden") and model_catalog_row_matches(row, DEFAULT_CODEX_MODEL)), None)
    if preferred is not None:
        return preferred
    for row in rows:
        model = model_catalog_row_model(row).casefold()
        text = " ".join(str(row.get(key) or "") for key in ("id", "model", "displayName", "description")).casefold()
        if not row.get("hidden") and model.startswith("gpt-") and ("mini" in text or "cost" in text):
            return row
    for row in rows:
        if not row.get("hidden") and model_catalog_row_model(row).casefold().startswith("gpt-"):
            return row
    for row in rows:
        if row.get("isDefault") is True:
            return row
    return rows[0] if rows else None


def resolved_model_settings_from_catalog(rows: list[dict[str, Any]], model: str, effort: str) -> tuple[str, str]:
    requested_model = model.strip()
    requested_effort = effort.strip().lower()
    resolved_model = requested_model
    resolved_effort = requested_effort
    matched_row = next((item for item in rows if model_catalog_row_matches(item, requested_model)), None) if requested_model else None
    row = matched_row or default_model_catalog_row(rows)
    if row is None:
        return resolved_model or DEFAULT_CODEX_MODEL, resolved_effort or DEFAULT_CODEX_EFFORT
    row_model = model_catalog_row_model(row)
    if row_model:
        resolved_model = row_model
    efforts = model_effort_values(row)
    row_default_effort = str(row.get("defaultReasoningEffort") or "").strip().lower()
    fallback_effort = DEFAULT_CODEX_EFFORT if not efforts or DEFAULT_CODEX_EFFORT in efforts else row_default_effort
    unsupported_model = bool(requested_model and matched_row is None)
    if not resolved_effort or unsupported_model or (efforts and resolved_effort not in efforts):
        resolved_effort = fallback_effort
    if not resolved_effort:
        resolved_effort = DEFAULT_CODEX_EFFORT
    return resolved_model, resolved_effort


def human_error_message(value: Any) -> str:
    if isinstance(value, dict):
        message = str(value.get("message") or value.get("detail") or value.get("error") or "").strip()
    else:
        message = str(value or "").strip()
    if not message:
        return ""
    try:
        decoded = json.loads(message)
    except json.JSONDecodeError:
        return message
    if isinstance(decoded, dict):
        return str(decoded.get("detail") or decoded.get("message") or message).strip()
    return message


def format_model_catalog_lines(rows: list[dict[str, Any]]) -> list[str]:
    lines = [
        "MODEL                         DEFAULT  EFFORTS                HIDDEN  NAME",
        "-----                         -------  -------                ------  ----",
    ]
    for row in rows:
        model = str(row.get("model") or row.get("id") or "").strip()
        name = str(row.get("displayName") or "").strip()
        default_effort = str(row.get("defaultReasoningEffort") or "").strip()
        effort_text = ",".join(model_effort_values(row))
        hidden = "yes" if row.get("hidden") else "no"
        lines.append(f"{model:<29} {default_effort:<8} {effort_text:<22} {hidden:<6} {name}")
    return lines


def build_config_help(model_rows: list[dict[str, Any]] | None = None, catalog_error: str = "") -> str:
    accepted_efforts = reasoning_efforts_text(REASONING_EFFORT_ORDER)
    reasoning_label = CODEX_OUTPUT_TERMS.lower_label
    lines = ["Models:"]
    if model_rows:
        lines.append("  All models reported by local Codex app-server, including hidden models:")
        lines.extend(f"  {line}" for line in format_model_catalog_lines(model_rows))
        catalog_efforts = reasoning_efforts_text(model_catalog_efforts(model_rows))
        if catalog_efforts and catalog_efforts != accepted_efforts:
            lines.append(f"  Catalog-advertised {reasoning_label} effort values: {catalog_efforts}")
    elif catalog_error:
        lines.append(f"  Live model catalog unavailable: {catalog_error}")
        lines.append("  Run /model inside the REPL after startup to query the local Codex app-server.")
    else:
        lines.append("  Live model catalog is queried from the local Codex app-server when --help or /help runs.")
        lines.append("  Run /model inside the REPL to query the same catalog manually.")
    lines.extend(
        [
            f"  Client accepted {reasoning_label} effort values: {accepted_efforts}",
            "  Select one with: -m gpt-5.5",
            f"  Select {reasoning_label} effort with: -c {CODEX_CONFIG_KEYS.effort}=\"low\"",
            "  Inside the REPL, use: /model gpt-5.5 low",
            "",
            "Common -c settings:",
            f"  -c {CODEX_CONFIG_KEYS.effort}=\"low\"       {reasoning_label} effort values: {accepted_efforts}",
            f"  -c model_reasoning_summary=\"concise\"  {reasoning_label} summary values: none, auto, concise, detailed; summary aliases to concise; default: concise",
            "  -c service_tier=\"fast\"                enable Fast mode",
            f"  -c approval_policy=\"{DEFAULT_APPROVAL_POLICY}\"            values: untrusted, on-failure, on-request, never; default: {DEFAULT_APPROVAL_POLICY}",
            f"  -c sandbox=\"{DEFAULT_SANDBOX}\"       values: read-only, workspace-write, danger-full-access; default: {DEFAULT_SANDBOX}",
            f"  -c bypass_hook_trust={str(DEFAULT_BYPASS_HOOK_TRUST).lower()}              default: {str(DEFAULT_BYPASS_HOOK_TRUST).lower()}",
            "  -c web_search=\"live\"                  enable live web search",
            f"  -c {CODEX_CONFIG_KEYS.hidden_work_summary}=false toggle {CODEX_OUTPUT_TERMS.summary_label}",
            f"  -c {CODEX_CONFIG_KEYS.hidden_work_raw}=true      toggle {CODEX_OUTPUT_TERMS.raw_label}",
            f"  -c {CODEX_CONFIG_KEYS.tool_output}=false",
            f"  -c {CODEX_CONFIG_KEYS.metrics}=true          print TTFT/token/tool timing after each turn",
            "  -c text_client.debug_json=true            dump raw JSON-RPC messages to stderr",
            f"  -c {CODEX_CONFIG_KEYS.session}=\"<id>\"        or use: resume <thread-id>",
            f"  -c {CODEX_CONFIG_KEYS.timeout}=300              app-server request timeout; during turns this is an idle timeout",
            f"  -c text_client.approval_mode=\"{DEFAULT_TEXT_CLIENT_APPROVAL_MODE}\" values: prompt, accept, accept-session, deny, abort; default: {DEFAULT_TEXT_CLIENT_APPROVAL_MODE}",
            "",
            "Inside the REPL:",
            f"  /config model_reasoning_summary=auto    show {CODEX_OUTPUT_TERMS.summary_label}",
            f"  /config model_reasoning_summary=concise show concise {CODEX_OUTPUT_TERMS.summary_label}",
            f"  /config {CODEX_CONFIG_KEYS.hidden_work_raw}=true show {CODEX_OUTPUT_TERMS.raw_label}",
        ]
    )
    return "\n".join(lines)


def read_help_model_message(process: subprocess.Popen[str], deadline: float) -> dict[str, Any]:
    if process.stdout is None:
        raise RuntimeError("codex app-server stdout is not available")
    timeout = max(0.0, deadline - time.monotonic())
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        if not selector.select(timeout):
            raise TimeoutError("timed out waiting for codex app-server model catalog")
    line = process.stdout.readline()
    if not line:
        raise RuntimeError("codex app-server exited while fetching model catalog")
    message = json.loads(line)
    if not isinstance(message, dict):
        raise RuntimeError("codex app-server emitted a non-object model catalog message")
    return message


def help_model_request(process: subprocess.Popen[str], request_id: str, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    if process.stdin is None:
        raise RuntimeError("codex app-server stdin is not available")
    process.stdin.write(json.dumps(json_rpc_request(request_id, method, params), separators=(",", ":")) + "\n")
    process.stdin.flush()
    deadline = time.monotonic() + HELP_MODEL_QUERY_TIMEOUT_SECONDS
    while True:
        message = read_help_model_message(process, deadline)
        if message.get("id") == request_id and ("result" in message or "error" in message):
            if message.get("error"):
                raise RuntimeError(f"{method} failed: {human_error_message(message.get('error')) or message.get('error')}")
            result = message.get("result")
            return result if isinstance(result, dict) else {}


def fetch_help_model_catalog() -> tuple[list[dict[str, Any]], str]:
    codex_path = shutil.which("codex")
    if not codex_path:
        return [], "codex CLI not found on PATH"
    process: subprocess.Popen[str] | None = None
    try:
        process = subprocess.Popen(
            [codex_path, "app-server", "--listen", "stdio://"],
            cwd=os.getcwd(),
            env=app_server_env(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        help_model_request(process, "help-initialize", "initialize", initialize_params())
        if process.stdin is None:
            raise RuntimeError("codex app-server stdin is not available")
        process.stdin.write(json.dumps(json_rpc_notification("initialized"), separators=(",", ":")) + "\n")
        process.stdin.flush()
        rows: list[dict[str, Any]] = []
        cursor = None
        request_index = 0
        while True:
            params: dict[str, Any] = {"includeHidden": True, "limit": 100}
            if cursor:
                params["cursor"] = cursor
            request_index += 1
            response = help_model_request(process, f"help-model-list-{request_index}", "model/list", params)
            data = response.get("data")
            if isinstance(data, list):
                rows.extend(item for item in data if isinstance(item, dict))
            cursor = response.get("nextCursor")
            if not cursor:
                break
        return rows, ""
    except (TimeoutError, RuntimeError, OSError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        return [], str(exc)
    finally:
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1)


def normalize_option_name(name: str) -> str:
    return name.strip().lstrip("-").replace("_", "-")


def option_attr(name: str) -> str:
    return normalize_option_name(name).replace("-", "_")


def normalize_reasoning_summary(value: Any) -> str:
    summary = str(value).strip().lower()
    summary = REASONING_SUMMARY_ALIASES.get(summary, summary)
    if summary not in REASONING_SUMMARIES:
        allowed = sorted([*REASONING_SUMMARIES, *REASONING_SUMMARY_ALIASES])
        raise ValueError(f"model_reasoning_summary, the {CODEX_OUTPUT_TERMS.lower_label} summary setting, must be one of: {', '.join(allowed)}")
    return summary


def turn_id_from_turn(turn: Any) -> str:
    if isinstance(turn, dict):
        return str(turn.get("id") or "").strip()
    return ""


class CodexTextClient(TextClientBase):
    def __init__(self, args: argparse.Namespace):
        super().__init__(str(args.cwd), prefixed_output_labels(CODEX_OUTPUT_TERMS))
        self.args = args
        self.process: subprocess.Popen[str] | None = None
        self.request_counter = 0
        self.thread_id = str(args.thread_id or "").strip()
        self.active_turn_id = ""
        self.exit_hint_printed = False
        self.answer_buffer: list[str] = []
        self.reasoning_summary_buffer: list[str] = []
        self.raw_reasoning_buffer: list[str] = []
        self.final_item_text = ""
        self.tool_output_item_ids: set[str] = set()
        self.last_normalized_events: list[dict[str, Any]] = []
        self.app_server_protocol = CodexAppServerProtocol()
        self.turn_error_message = ""
        self.turn_error_printed = False
        self.reasoning_summary_unsupported_models = {normalized_model_id(item) for item in CODEX_MODELS_WITHOUT_REASONING_SUMMARY}

    def next_id(self, prefix: str) -> str:
        self.request_counter += 1
        return f"{prefix}-{self.request_counter}"

    def start(self) -> None:
        codex_path = shutil.which("codex")
        if not codex_path:
            raise RuntimeError("codex CLI not found on PATH")
        command = [codex_path, "app-server", "--listen", "stdio://"]
        if self.args.dangerously_bypass_hook_trust:
            command.extend(["-c", f"bypass_hook_trust={str(DEFAULT_BYPASS_HOOK_TRUST).lower()}"])
        if self.args.effort:
            command.extend(["-c", f'{CODEX_CONFIG_KEYS.effort}="{self.args.effort}"'])
        if self.args.service_tier:
            command.extend(["-c", f'service_tier="{self.args.service_tier}"'])
        self.process = subprocess.Popen(
            command,
            cwd=str(Path(self.args.cwd).expanduser().resolve()),
            env=app_server_env(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        response = self.request("initialize", initialize_params())
        self.write(json_rpc_notification("initialized"))
        self.resolve_default_model_settings()
        if self.args.debug_json:
            print(f"[initialized] {json.dumps(response, sort_keys=True)}", file=sys.stderr)

    def close(self) -> None:
        if self.process is None:
            return
        process = self.process
        self.process = None
        if process.poll() is None:
            self.app_server_protocol.terminate_process(process)

    def app_server_exit_details(self) -> str:
        process = self.process
        if process is None:
            return "codex app-server is not running"
        exit_code = process.poll()
        parts = ["codex app-server closed its pipe"]
        if exit_code is not None:
            parts.append(f"exit={exit_code}")
            if process.stderr is not None:
                stderr_text = process.stderr.read().strip()
                if stderr_text:
                    tail = "\n".join(stderr_text.splitlines()[-20:])
                    parts.append(f"stderr:\n{tail}")
        return "; ".join(parts)

    def restart_after_timeout(self, exc: TimeoutError) -> None:
        self.finish_prefixed_output()
        self.finish_answer_output()
        self.print_aux_stderr(f"codex app-server timeout: {exc}")
        if self.active_turn_id:
            try:
                self.interrupt_turn()
            except (RuntimeError, OSError):
                pass
        self.active_turn_id = ""
        self.close()
        try:
            self.start()
        except (TimeoutError, RuntimeError, OSError) as start_exc:
            self.close()
            self.print_aux_stderr(f"failed to restart codex app-server: {start_exc}")
            return
        if self.thread_id:
            self.print_aux_stderr("restarted codex app-server; next turn will resume the current thread")

    def print_exit_hint(self) -> None:
        if self.exit_hint_printed or not self.thread_id:
            return
        self.exit_hint_printed = True
        self.print_aux_stderr(f"[thread] {self.thread_id}")
        self.print_aux_stderr(f"[resume] {self.resume_command()}")

    def resume_command(self) -> str:
        command = ["python3", str(Path(__file__).resolve())]
        if self.args.model:
            command.extend(["-m", self.args.model])
        for image in self.args.image:
            command.extend(["--image", image])
        if self.args.oss:
            command.append("--oss")
        if self.args.local_provider:
            command.extend(["--local-provider", self.args.local_provider])
        if self.args.profile:
            command.extend(["--profile", self.args.profile])
        if CLIENT_PERMISSION_DEFAULTS.codex_is_permissive(self.args.sandbox, self.args.approval_policy):
            command.append(CODEX_BYPASS_APPROVALS_FLAG)
        elif self.args.sandbox != DEFAULT_SANDBOX:
            command.extend(["--sandbox", self.args.sandbox])
        if not CLIENT_PERMISSION_DEFAULTS.codex_is_permissive(self.args.sandbox, self.args.approval_policy) and self.args.approval_policy != DEFAULT_APPROVAL_POLICY:
            command.extend(["--ask-for-approval", self.args.approval_policy])
        if self.args.dangerously_bypass_hook_trust:
            command.append(CODEX_BYPASS_HOOK_TRUST_FLAG)
        command.extend(["-C", str(Path(self.args.cwd).expanduser().resolve())])
        for add_dir in self.args.add_dir:
            command.extend(["--add-dir", str(Path(add_dir).expanduser().resolve())])
        if self.args.search:
            command.append("--search")
        if self.args.no_alt_screen:
            command.append("--no-alt-screen")
        if self.args.strict_config:
            command.append("--strict-config")
        if self.args.remote:
            command.extend(["--remote", self.args.remote])
        if self.args.remote_auth_token_env:
            command.extend(["--remote-auth-token-env", self.args.remote_auth_token_env])
        for key, value in self.resume_config_values().items():
            command.extend(["-c", f"{key}={config_value_text(value)}"])
        command.extend(["resume", self.thread_id])
        return command_text(command)

    def resume_config_values(self) -> dict[str, Any]:
        values = dict(self.args.config_values)
        values.pop("approval_policy", None)
        values.pop(CODEX_CONFIG_KEYS.model, None)
        values.pop("sandbox", None)
        values.pop("sandbox_mode", None)
        values.pop(CODEX_CONFIG_KEYS.session, None)
        values.pop("web_search", None)
        if self.args.effort:
            values[CODEX_CONFIG_KEYS.effort] = self.args.effort
        if self.args.reasoning_summary != "concise" or "model_reasoning_summary" in self.args.config_values:
            values["model_reasoning_summary"] = self.args.reasoning_summary
        if self.args.service_tier:
            values["service_tier"] = self.args.service_tier
        client_defaults = {
            "text_client.approval_mode": DEFAULT_TEXT_CLIENT_APPROVAL_MODE,
            "text_client.debug_json": False,
            "text_client.include_hidden_models": False,
            CODEX_CONFIG_KEYS.raw_output: True,
            CODEX_CONFIG_KEYS.metrics: False,
            CODEX_CONFIG_KEYS.hidden_work_raw: False,
            CODEX_CONFIG_KEYS.hidden_work_summary: True,
            CODEX_CONFIG_KEYS.tool_output: True,
            CODEX_CONFIG_KEYS.timeout: APP_SERVER_TIMEOUT_SECONDS,
        }
        client_values = {
            "text_client.approval_mode": self.args.approval_mode,
            "text_client.debug_json": self.args.debug_json,
            "text_client.include_hidden_models": self.args.include_hidden_models,
            CODEX_CONFIG_KEYS.raw_output: self.args.raw_output,
            CODEX_CONFIG_KEYS.metrics: self.args.show_metrics,
            CODEX_CONFIG_KEYS.hidden_work_raw: self.args.show_raw_reasoning,
            CODEX_CONFIG_KEYS.hidden_work_summary: self.args.show_reasoning_summary,
            CODEX_CONFIG_KEYS.tool_output: self.args.show_tool_output,
            CODEX_CONFIG_KEYS.timeout: self.args.timeout,
        }
        for key, value in client_values.items():
            if value != client_defaults[key] or key in self.args.config_values:
                values[key] = value
        return dict(sorted(values.items()))

    def session_file_path(self) -> str:
        if not self.thread_id:
            return "<not started>"
        sessions_root = Path.home() / ".codex" / "sessions"
        if not sessions_root.exists():
            return f"<not found under {sessions_root}>"
        matches = sorted(sessions_root.rglob(f"*{self.thread_id}.jsonl"), key=lambda path: path.stat().st_mtime, reverse=True)
        if not matches:
            return f"<not found under {sessions_root}>"
        return str(matches[0].resolve())

    def write(self, message: dict[str, Any]) -> None:
        if self.process is None or self.process.stdin is None:
            raise RuntimeError("codex app-server is not running")
        if self.process.poll() is not None:
            raise RuntimeError(self.app_server_exit_details())
        try:
            self.app_server_protocol.write_message(self.process, message)
        except (BrokenPipeError, ConnectionResetError) as exc:
            raise RuntimeError(self.app_server_exit_details()) from exc
        if self.args.debug_json:
            print(f"> {json.dumps(message, sort_keys=True)}", file=sys.stderr)

    def readline(self, deadline: float) -> str:
        if self.process is None or self.process.stdout is None:
            raise RuntimeError("codex app-server is not running")
        try:
            return self.app_server_protocol.readline(self.process, deadline)
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError("timed out waiting for codex app-server") from exc

    def read_message(self, deadline: float) -> dict[str, Any]:
        if self.process is None or self.process.stdout is None:
            raise RuntimeError("codex app-server is not running")
        try:
            message = self.app_server_protocol.read_message(self.process, deadline)
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError("timed out waiting for codex app-server") from exc
        except OSError as exc:
            raise RuntimeError(str(exc)) from exc
        if self.args.debug_json:
            print(f"< {json.dumps(message, sort_keys=True)}", file=sys.stderr)
        return message

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        request_id = self.next_id(method.replace("/", "-"))
        deadline = time.monotonic() + self.args.timeout
        self.write(json_rpc_request(request_id, method, params))
        while True:
            message = self.read_message(deadline)
            if message.get("id") == request_id and ("result" in message or "error" in message):
                if message.get("error"):
                    raise RuntimeError(f"{method} failed: {human_error_message(message.get('error')) or message.get('error')}")
                result = message.get("result")
                if self.current_metrics is not None:
                    collect_token_usage(result, self.current_metrics)
                return result if isinstance(result, dict) else {}
            self.handle_async_message(message, deadline)
            deadline = time.monotonic() + self.args.timeout

    def model_catalog_rows(self, include_hidden: bool) -> list[dict[str, Any]]:
        cursor = None
        rows: list[dict[str, Any]] = []
        while True:
            params: dict[str, Any] = {"includeHidden": include_hidden, "limit": 100}
            if cursor:
                params["cursor"] = cursor
            response = self.request("model/list", params)
            data = response.get("data")
            if isinstance(data, list):
                rows.extend(item for item in data if isinstance(item, dict))
            cursor = response.get("nextCursor")
            if not cursor:
                break
        return rows

    def resolve_default_model_settings(self) -> None:
        requested_model = str(self.args.model or "").strip()
        requested_effort = str(self.args.effort or "").strip().lower()
        try:
            rows = self.model_catalog_rows(include_hidden=True)
        except (TimeoutError, RuntimeError, OSError, json.JSONDecodeError) as exc:
            self.print_aux_stderr(f"could not resolve Codex default model settings: {exc}")
            if not self.args.model:
                self.args.model = DEFAULT_CODEX_MODEL
            if not self.args.effort:
                self.args.effort = DEFAULT_CODEX_EFFORT
            self.sync_resolved_model_settings_config()
            return
        model, effort = resolved_model_settings_from_catalog(rows, self.args.model, self.args.effort)
        if model:
            self.args.model = model
        if effort:
            self.args.effort = effort
        self.sync_resolved_model_settings_config()
        if requested_model and requested_model != self.args.model:
            self.print_aux_stderr(f"Codex model {requested_model} is not available; using {self.args.model}[{self.args.effort}]")
        elif requested_effort and requested_effort != self.args.effort:
            self.print_aux_stderr(f"Codex {CODEX_OUTPUT_TERMS.lower_label} effort {requested_effort} is not available for {self.args.model}; using {self.args.effort}")

    def sync_resolved_model_settings_config(self) -> None:
        if self.args.effort:
            self.args.config_values[CODEX_CONFIG_KEYS.effort] = self.args.effort
        if CODEX_CONFIG_KEYS.model in self.args.config_values:
            self.args.config_values[CODEX_CONFIG_KEYS.model] = self.args.model

    def current_model_supports_reasoning_summary(self) -> bool:
        return codex_model_supports_reasoning_summary(self.args.model, self.reasoning_summary_unsupported_models)

    def register_reasoning_summary_unsupported(self, model: str) -> None:
        model_id = normalized_model_id(model)
        if model_id:
            self.reasoning_summary_unsupported_models.add(model_id)

    def reasoning_summary_for_current_model(self) -> str:
        if not self.current_model_supports_reasoning_summary():
            return ""
        return str(self.args.reasoning_summary or "").strip()

    def list_models(self, include_hidden: bool | None = None) -> None:
        rows = self.model_catalog_rows(bool(self.args.include_hidden_models) if include_hidden is None else include_hidden)
        if not rows:
            print("No models returned by codex app-server.")
            return
        for line in format_model_catalog_lines(rows):
            print(line)

    def handle_repl_command(self, text: str) -> str:
        body = text[1:].strip()
        command, _, rest = body.partition(" ")
        command = normalize_option_name(command).lower()
        if not command:
            return "handled"
        if command in {"q", "quit", "exit"}:
            return "quit"
        if command == "help":
            self.print_repl_help()
            return "handled"
        if command == "status":
            self.print_status()
            return "handled"
        if command == "context":
            self.print_context()
            return "handled"
        if command == "clear":
            self.clear_conversation()
            return "handled"
        if command == "cls":
            print("\033c", end="")
            return "handled"
        if command == "model":
            self.handle_model_command(rest)
            return "handled"
        if command == "effort":
            self.handle_effort_command(rest)
            return "handled"
        if command in {"permission", "permission-mode", "permissions"}:
            self.handle_permissions_command(rest, command)
            return "handled"
        if command in {"reasoning", "thinking"}:
            self.handle_reasoning_command(rest, command)
            return "handled"
        if command == "fast":
            self.handle_fast_command(rest)
            return "handled"
        if command == "config":
            self.handle_config_command(rest)
            return "handled"
        if command == "metrics":
            self.handle_metrics_command(rest)
            return "handled"
        if command == "usage":
            self.print_usage()
            return "handled"
        if command == "raw":
            self.handle_raw_command(rest)
            return "handled"
        if command == "resume":
            self.handle_resume_command(rest)
            return "handled"
        if command in UNIMPLEMENTED_CODEX_COMMANDS:
            print(f"/{command}: {UNIMPLEMENTED_CODEX_COMMANDS[command]}")
            return "handled"
        print(f"unknown command: /{command}")
        print("run /help for Codex slash commands")
        return "handled"

    def print_repl_help(self) -> None:
        print("Slash commands:")
        for row in client_slash_help_rows("codex"):
            print(row)
        print("Recognized but not implemented in this text prototype:")
        print("  " + " ".join(f"/{name}" for name in sorted(UNIMPLEMENTED_CODEX_COMMANDS)))
        try:
            model_rows = self.model_catalog_rows(include_hidden=True)
            print(build_config_help(model_rows))
        except (TimeoutError, RuntimeError, OSError) as exc:
            print(build_config_help([], str(exc)))
        print("Keyboard shortcuts:")
        print("  Ctrl-A start, Ctrl-E end, Ctrl-K kill to end, Ctrl-U kill line")
        print("  Ctrl-W kill word, Ctrl-Y yank, Ctrl-P/N history, Ctrl-R history search")
        print("  Alt-B/F move word, Alt-D kill word, Tab completes slash commands")

    def print_status(self) -> None:
        print("OpenAI Codex")
        print(f"  Model:                {display_value(self.args.model)} ({CODEX_OUTPUT_TERMS.title_label} {display_value(self.args.effort)}, summaries {self.args.reasoning_summary})")
        print("  Model provider:       Codex app-server")
        print(f"  Directory:            {self.display_cwd()}")
        print(f"  Permissions:          {self.permissions_text()}")
        print(f"  Agents.md:            {self.agents_summary()}")
        print("  Account:              managed by Codex app-server")
        print("  Collaboration mode:   Default")
        print(f"  Session:              {self.thread_id or '<not started>'}")
        print(f"  Session file:         {self.session_file_path()}")
        print(f"  Fast mode:            {'on' if self.args.service_tier == 'fast' else 'off'}")
        print(f"  Raw output:           {'on' if self.args.raw_output else 'off'}")
        print(f"  {CODEX_OUTPUT_TERMS.show_label}: {'on' if self.args.show_reasoning_summary else 'off'} summaries, {'on' if self.args.show_raw_reasoning else 'off'} raw")
        print(f"  Show tool output:     {'on' if self.args.show_tool_output else 'off'}")
        print(f"  Metrics:              {'on' if self.args.show_metrics else 'off'}")
        print(f"  Terminal bg:          {self.terminal_background_text()}")
        print("  Limits:               run real Codex /status for account usage limits")

    def print_context(self) -> None:
        print("Codex Context")
        print(f"  Directory:            {self.display_cwd()}")
        print(f"  Thread:               {self.thread_id or '<not started>'}")
        print(f"  Session file:         {self.session_file_path()}")
        print(f"  Model:                {display_value(self.args.model)}")
        print(f"  {CODEX_OUTPUT_TERMS.title_label} effort: {display_value(self.args.effort)}")
        print(f"  Summary mode:         {self.args.reasoning_summary}")
        print(f"  Permissions:          {self.permissions_text()}")
        print(f"  Sandbox:              {self.args.sandbox}")
        print(f"  Approval policy:      {self.args.approval_policy}")
        print(f"  Add dirs:             {', '.join(self.args.add_dir) if self.args.add_dir else '<none>'}")
        print(f"  Output toggles:       summary={'on' if self.args.show_reasoning_summary else 'off'}, raw={'on' if self.args.show_raw_reasoning else 'off'}, tool={'on' if self.args.show_tool_output else 'off'}, metrics={'on' if self.args.show_metrics else 'off'}")
        if self.last_metrics is not None:
            print("  Last metrics:")
            for line in self.metrics_lines(self.last_metrics):
                print(f"    {line}")

    def clear_conversation(self) -> None:
        self.thread_id = ""
        self.args.thread_id = ""
        self.active_turn_id = ""
        self.answer_buffer = []
        self.reasoning_summary_buffer = []
        self.raw_reasoning_buffer = []
        self.final_item_text = ""
        self.last_metrics = None
        print("Conversation cleared; next turn starts a new Codex thread.")

    def print_usage(self) -> None:
        print("Codex Usage")
        metrics = self.last_metrics
        if metrics is None:
            print("  No completed turn yet.")
            return
        if metrics.usage:
            for key in sorted(metrics.usage):
                print(f"  {key}: {metrics.usage[key]:g}")
        else:
            print("  Backend did not emit token usage for the last turn.")
        print("  Metrics:")
        for line in self.metrics_lines(metrics):
            print(f"    {line}")

    def handle_model_command(self, rest: str) -> None:
        self.print_compat_note("model", "Codex", "Implemented here to match the Claude-style runtime model switch.")
        parts = shlex.split(rest)
        if not parts:
            self.list_models()
            return
        model = parts[0]
        effort = parts[1] if len(parts) > 1 else self.args.effort
        if effort and effort not in REASONING_EFFORTS:
            print(f"{CODEX_OUTPUT_TERMS.lower_label} effort must be one of: {', '.join(sorted(REASONING_EFFORTS))}")
            return
        self.args.model = model
        self.args.effort = effort
        self.update_thread_settings(model=model, effort=effort or None)
        print(f"model changed: {model}" + (f" ({effort})" if effort else ""))

    def handle_effort_command(self, rest: str) -> None:
        self.print_compat_note("effort", "Codex", f"Claude has a native /effort command; codex.py maps it to Codex {CODEX_OUTPUT_TERMS.lower_label} effort.")
        parts = shlex.split(rest)
        if not parts:
            print(f"{CODEX_OUTPUT_TERMS.title_label} effort: {display_value(self.args.effort)}")
            print(f"Usage: /effort [{'|'.join(sorted(REASONING_EFFORTS))}]")
            return
        effort = parts[0].strip().lower()
        if effort not in REASONING_EFFORTS:
            print(f"{CODEX_OUTPUT_TERMS.lower_label} effort must be one of: {', '.join(sorted(REASONING_EFFORTS))}")
            return
        self.args.effort = effort
        self.update_thread_settings(effort=effort)
        print(f"{CODEX_OUTPUT_TERMS.lower_label} effort changed: {effort}")

    def handle_permissions_command(self, rest: str, command: str = "permissions") -> None:
        if command in {"permission", "permission-mode"}:
            self.print_compat_note(command, "Codex", slash_command_compat_note("codex", command))
        parts = shlex.split(rest)
        if not parts:
            print(f"Permissions: {self.permissions_text()}")
            print(f"Usage: /{command} [read-only|workspace-write|{DEFAULT_SANDBOX}|untrusted|on-failure|on-request|never|{CODEX_YOLO_ALIAS}]")
            return
        for value in parts:
            normalized = value.strip().lower()
            if normalized == CODEX_YOLO_ALIAS:
                self.args.sandbox = DEFAULT_SANDBOX
                self.args.approval_policy = DEFAULT_APPROVAL_POLICY
                continue
            if normalized in SANDBOX_MODES:
                self.args.sandbox = normalized
                continue
            if normalized in APPROVAL_POLICIES:
                self.args.approval_policy = normalized
                continue
            print(f"unknown permissions value: {value}")
            return
        self.update_thread_settings(
            approvalPolicy=self.args.approval_policy,
            sandboxPolicy=self.sandbox_policy_param(),
        )
        print(f"Permissions: {self.permissions_text()}")

    def handle_reasoning_command(self, rest: str, command: str = "reasoning") -> None:
        if command == "thinking":
            self.print_compat_note(command, "Codex", slash_command_compat_note("codex", command))
        value = rest.strip().lower()
        if not value:
            print(f"{CODEX_OUTPUT_TERMS.title_label}: summary={self.args.reasoning_summary}, show_summary={'on' if self.args.show_reasoning_summary else 'off'}, raw={'on' if self.args.show_raw_reasoning else 'off'}")
            print("Usage: /reasoning [on|off|summary|raw|none|auto|concise|detailed]")
            return
        if value == "on":
            self.args.show_reasoning_summary = True
        elif value == "off":
            self.args.show_reasoning_summary = False
            self.args.show_raw_reasoning = False
        elif value == "summary":
            self.args.show_reasoning_summary = True
            self.args.show_raw_reasoning = False
            if self.args.reasoning_summary == "none":
                self.args.reasoning_summary = "concise"
        elif value == "raw":
            self.args.show_raw_reasoning = True
        elif value in REASONING_SUMMARIES | set(REASONING_SUMMARY_ALIASES):
            self.args.reasoning_summary = normalize_reasoning_summary(value)
            self.args.show_reasoning_summary = self.args.reasoning_summary != "none"
            if self.args.reasoning_summary == "none":
                self.args.show_raw_reasoning = False
        else:
            print(f"reasoning mode must be one of: on, off, summary, raw, {', '.join(sorted(REASONING_SUMMARIES))}")
            return
        self.args.config_values["model_reasoning_summary"] = self.args.reasoning_summary
        self.args.config_values[CODEX_CONFIG_KEYS.hidden_work_summary] = self.args.show_reasoning_summary
        self.args.config_values[CODEX_CONFIG_KEYS.hidden_work_raw] = self.args.show_raw_reasoning
        runtime_settings = self.thread_settings_for_config_key("model_reasoning_summary")
        if runtime_settings:
            self.update_thread_settings(**runtime_settings)
        elif self.thread_id:
            self.print_aux_stderr(f"{self.args.model} does not support Codex {CODEX_OUTPUT_TERMS.lower_label} summaries; stored locally and omitted from app-server settings")
        print(f"{CODEX_OUTPUT_TERMS.title_label}: summary={self.args.reasoning_summary}, show_summary={'on' if self.args.show_reasoning_summary else 'off'}, raw={'on' if self.args.show_raw_reasoning else 'off'}")

    def handle_fast_command(self, rest: str) -> None:
        value = rest.strip().lower()
        try:
            enabled = True if not value else parse_bool(value)
        except ValueError as exc:
            print(str(exc))
            return
        self.args.service_tier = "fast" if enabled else ""
        self.update_thread_settings(serviceTier=self.args.service_tier or None)
        print(f"Fast mode {'on' if enabled else 'off'}")

    def handle_raw_command(self, rest: str) -> None:
        self.print_compat_note("raw", "Codex", "Implemented by this text client to toggle clone-specific raw output state.")
        value = rest.strip().lower()
        try:
            self.args.raw_output = not self.args.raw_output if not value else parse_bool(value)
        except ValueError as exc:
            print(str(exc))
            return
        print(f"Raw output {'on' if self.args.raw_output else 'off'}")

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
        self.args.config_values[CODEX_CONFIG_KEYS.metrics] = self.args.show_metrics
        print(f"Metrics {'on' if self.args.show_metrics else 'off'}")

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
            if self.args.strict_config and key not in KNOWN_CONFIG_KEYS and not key.startswith("features."):
                print(f"unrecognized config key: {key}")
                return
            value = parse_config_value(raw_value)
            try:
                set_config_override(self.args, key, value)
            except ValueError as exc:
                print(str(exc))
                return
            self.args.config_values[key] = normalized_config_value(key, value)
            if key == CODEX_CONFIG_KEYS.session:
                self.thread_id = self.args.thread_id
            runtime_settings = self.thread_settings_for_config_key(key)
            if runtime_settings:
                self.update_thread_settings(**runtime_settings)
            elif self.thread_id and not key.startswith("text_client."):
                print(f"{key} stored locally; existing thread settings were not changed")
            print(f"{key} = {config_value_text(self.args.config_values[key])}")

    def print_config_settings(self) -> None:
        rows = {
            CODEX_CONFIG_KEYS.model: self.args.model,
            CODEX_CONFIG_KEYS.effort: self.args.effort,
            "model_reasoning_summary": self.args.reasoning_summary,
            "approval_policy": self.args.approval_policy,
            "bypass_hook_trust": self.args.dangerously_bypass_hook_trust,
            "sandbox": self.args.sandbox,
            "service_tier": self.args.service_tier,
            "web_search": self.args.search,
            CODEX_CONFIG_KEYS.hidden_work_summary: self.args.show_reasoning_summary,
            CODEX_CONFIG_KEYS.hidden_work_raw: self.args.show_raw_reasoning,
            CODEX_CONFIG_KEYS.tool_output: self.args.show_tool_output,
            CODEX_CONFIG_KEYS.metrics: self.args.show_metrics,
            CODEX_CONFIG_KEYS.timeout: self.args.timeout,
            CODEX_CONFIG_KEYS.session: self.thread_id,
            "text_client.approval_mode": self.args.approval_mode,
            "text_client.include_hidden_models": self.args.include_hidden_models,
            CODEX_CONFIG_KEYS.raw_output: self.args.raw_output,
            "text_client.debug_json": self.args.debug_json,
        }
        for key, value in rows.items():
            print(f"{key} = {config_value_text(value)}")

    def handle_resume_command(self, rest: str) -> None:
        thread_id = rest.strip()
        if not thread_id:
            print("Usage: /resume <thread-id>")
            return
        self.thread_id = thread_id
        self.args.thread_id = thread_id
        print(f"Session: {self.thread_id}")

    def update_thread_settings(self, **settings: Any) -> bool:
        if not self.thread_id:
            return False
        params = {"threadId": self.thread_id}
        params.update(settings)
        try:
            self.request("thread/settings/update", params)
        except RuntimeError as exc:
            if thread_not_found_error(str(exc)):
                self.print_aux_stderr(f"thread {self.thread_id} is not loaded in this Codex app-server; stored settings locally and will apply on the next turn")
                return False
            raise
        return True

    def thread_settings_for_config_key(self, key: str) -> dict[str, Any]:
        if key == CODEX_CONFIG_KEYS.model:
            return {"model": self.args.model or None}
        if key == CODEX_CONFIG_KEYS.effort:
            return {"effort": self.args.effort or None}
        if key == "model_reasoning_summary":
            summary = self.reasoning_summary_for_current_model()
            return {"summary": summary or None} if summary else {}
        if key == "approval_policy":
            return {"approvalPolicy": self.args.approval_policy}
        if key in {"sandbox", "sandbox_mode", "web_search"}:
            return {"sandboxPolicy": self.sandbox_policy_param()}
        if key == "service_tier":
            return {"serviceTier": self.args.service_tier or None}
        return {}

    def prompt_text(self) -> str:
        model = self.args.model or "codex"
        effort = self.args.effort
        return self.prompt_text_for(model, effort)

    def permissions_text(self) -> str:
        if CLIENT_PERMISSION_DEFAULTS.codex_is_permissive(self.args.sandbox, self.args.approval_policy):
            return "YOLO mode"
        labels = {
            "read-only": "Read Only",
            "workspace-write": "Workspace Write",
            DEFAULT_SANDBOX: "Danger Full Access",
        }
        return f"{labels[self.args.sandbox]} ({self.args.approval_policy})"

    def agents_summary(self) -> str:
        paths: list[str] = []
        global_agents = Path.home() / ".codex" / "AGENTS.md"
        if global_agents.exists():
            paths.append(str(global_agents))
        cwd = Path(self.args.cwd).expanduser().resolve()
        for candidate_dir in [cwd, *cwd.parents]:
            candidate = candidate_dir / "AGENTS.md"
            if candidate.exists():
                paths.append(str(candidate))
                break
        return ", ".join(paths) if paths else "<none>"

    def sandbox_policy_param(self) -> dict[str, Any]:
        if self.args.sandbox == DEFAULT_SANDBOX:
            return {"type": "dangerFullAccess"}
        if self.args.sandbox == "workspace-write":
            return {"type": "workspaceWrite", "networkAccess": bool(self.args.search), "writableRoots": [str(Path(self.args.cwd).expanduser().resolve())]}
        return {"type": "readOnly", "networkAccess": bool(self.args.search)}

    def emit_tool_item(self, event: str, item: dict[str, Any]) -> None:
        if not self.args.show_tool_output:
            return
        summary = self.tool_item_summary(event, item)
        if summary:
            head, _, tail = summary.partition(": ")
            if tail:
                self.write_prefixed_highlighted_line(TOOL_OUTPUT_PREFIX, head, tail)
            else:
                self.write_prefixed_line(TOOL_OUTPUT_PREFIX, summary)
        if event == "done" and item.get("type") == "commandExecution":
            item_id = str(item.get("id") or "").strip()
            aggregated_output = str(item.get("aggregatedOutput") or "")
            if aggregated_output and item_id not in self.tool_output_item_ids:
                self.write_prefixed_line(TOOL_OUTPUT_PREFIX, "output:")
                self.write_prefixed_stdout(TOOL_OUTPUT_PREFIX, aggregated_output)

    def tool_item_summary(self, event: str, item: dict[str, Any]) -> str:
        item_type = str(item.get("type") or "")
        if item_type == "commandExecution":
            command = str(item.get("command") or "").strip()
            status = str(item.get("status") or "").strip()
            line = f"{event} command"
            if command:
                line += f": {command}"
            if event == "done":
                exit_code = item.get("exitCode")
                duration_ms = item.get("durationMs")
                details = [detail for detail in [f"status={status}" if status else "", f"exit={exit_code}" if exit_code is not None else "", f"duration_ms={duration_ms}" if duration_ms is not None else ""] if detail]
                if details:
                    line += f" ({', '.join(details)})"
            return line
        if item_type == "mcpToolCall":
            server = str(item.get("server") or "").strip()
            tool = str(item.get("tool") or "").strip()
            status = str(item.get("status") or "").strip()
            target = ".".join(part for part in [server, tool] if part)
            line = f"{event} mcp"
            if target:
                line += f": {target}"
            if event == "done" and status:
                line += f" ({status})"
            return line
        if item_type == "dynamicToolCall":
            namespace = str(item.get("namespace") or "").strip()
            tool = str(item.get("tool") or "").strip()
            status = str(item.get("status") or "").strip()
            target = ".".join(part for part in [namespace, tool] if part)
            line = f"{event} tool"
            if target:
                line += f": {target}"
            if event == "done" and status:
                line += f" ({status})"
            return line
        if item_type == "webSearch":
            query = str(item.get("query") or "").strip()
            line = f"{event} web-search"
            if query:
                line += f": {query}"
            return line
        if item_type == "fileChange":
            status = str(item.get("status") or "").strip()
            return f"{event} file-change" + (f" ({status})" if status else "")
        return ""

    def ensure_thread(self) -> str:
        if self.thread_id:
            params = {"threadId": self.thread_id, **self.thread_params()}
            response = self.request("thread/resume", params)
        else:
            response = self.request("thread/start", self.thread_params())
        thread = response.get("thread") if isinstance(response.get("thread"), dict) else {}
        self.thread_id = str(thread.get("id") or self.thread_id).strip()
        if not self.thread_id:
            raise RuntimeError("codex app-server did not return a thread id")
        self.print_aux_stderr(f"[thread] {self.thread_id}")
        return self.thread_id

    def thread_params(self) -> dict[str, Any]:
        params: dict[str, Any] = {
            "approvalPolicy": self.args.approval_policy,
            "approvalsReviewer": "user",
            "cwd": str(Path(self.args.cwd).expanduser().resolve()),
            "config": self.args.config_values,
            "ephemeral": bool(self.args.ephemeral),
            "runtimeWorkspaceRoots": [str(Path(path).expanduser().resolve()) for path in [self.args.cwd, *self.args.add_dir]],
            "sandbox": self.args.sandbox,
        }
        if self.args.model:
            params["model"] = self.args.model
        if self.args.base_instructions:
            params["baseInstructions"] = self.args.base_instructions
        if self.args.service_tier:
            params["serviceTier"] = self.args.service_tier
        return params

    def turn_params(self, text: str) -> dict[str, Any]:
        params: dict[str, Any] = {
            "threadId": self.thread_id,
            "approvalPolicy": self.args.approval_policy,
            "input": [{"type": "text", "text": text, "text_elements": []}],
            "cwd": str(Path(self.args.cwd).expanduser().resolve()),
            "runtimeWorkspaceRoots": [str(Path(path).expanduser().resolve()) for path in [self.args.cwd, *self.args.add_dir]],
            "sandboxPolicy": self.sandbox_policy_param(),
        }
        summary = self.reasoning_summary_for_current_model()
        if summary:
            params["summary"] = summary
        if self.args.effort:
            params["effort"] = self.args.effort
        if self.args.model:
            params["model"] = self.args.model
        if self.args.service_tier:
            params["serviceTier"] = self.args.service_tier
        return params

    def send_turn(self, text: str) -> bool:
        if not text.strip():
            return True
        self.start_metrics(text)
        try:
            self.ensure_thread()
            if self.current_metrics is not None:
                self.current_metrics.thread_ready_at = time.monotonic()
            self.answer_buffer = []
            self.reasoning_summary_buffer = []
            self.raw_reasoning_buffer = []
            self.final_item_text = ""
            self.turn_error_message = ""
            self.turn_error_printed = False
            self.prefixed_output_at_line_start = {label: True for label in prefixed_output_labels(CODEX_OUTPUT_TERMS)}
            self.answer_output_at_line_start = True
            self.tool_output_item_ids = set()
            if self.current_metrics is not None:
                self.current_metrics.turn_start_request_at = time.monotonic()
            turn_params = self.turn_params(text)
            try:
                turn_response = self.request("turn/start", turn_params)
            except RuntimeError as exc:
                if "summary" in turn_params and reasoning_summary_unsupported_error(str(exc)):
                    self.register_reasoning_summary_unsupported(self.args.model)
                    self.print_aux_stderr(f"{self.args.model} does not support Codex {CODEX_OUTPUT_TERMS.lower_label} summaries; retrying without summary")
                    turn_response = self.request("turn/start", self.turn_params(text))
                else:
                    raise
            if self.current_metrics is not None:
                self.current_metrics.turn_start_response_at = time.monotonic()
                collect_token_usage(turn_response, self.current_metrics)
            self.active_turn_id = turn_id_from_turn(turn_response.get("turn")) or self.active_turn_id
            if self.args.debug_json:
                print(f"[turn] {json.dumps(turn_response, sort_keys=True)}", file=sys.stderr)
            self.wait_for_turn()
            ok = not self.turn_error_message
            self.finish_metrics("complete" if ok else "error", self.args.show_metrics)
            return ok
        except TimeoutError as exc:
            self.finish_metrics("timeout", self.args.show_metrics)
            self.restart_after_timeout(exc)
            return False
        except (RuntimeError, OSError, json.JSONDecodeError) as exc:
            self.finish_metrics("error", self.args.show_metrics)
            self.finish_prefixed_output()
            self.finish_answer_output()
            self.print_aux_stderr(f"codex app-server error: {exc}")
            self.active_turn_id = ""
            if self.process is not None and self.process.poll() is not None:
                self.close()
                try:
                    self.start()
                except (TimeoutError, RuntimeError, OSError, json.JSONDecodeError) as start_exc:
                    self.close()
                    self.print_aux_stderr(f"failed to restart codex app-server: {start_exc}")
                else:
                    self.print_aux_stderr("restarted codex app-server; try the turn again")
            return False
        except KeyboardInterrupt:
            self.finish_metrics("interrupted", self.args.show_metrics)
            self.interrupt_turn()
            self.finish_prefixed_output()
            self.finish_answer_output()
            self.print_aux_stderr("interrupted current turn; returned to prompt")
            return False

    def wait_for_turn(self) -> None:
        deadline = time.monotonic() + self.args.timeout
        while True:
            message = self.read_message(deadline)
            if self.handle_async_message(message, deadline):
                return
            deadline = time.monotonic() + self.args.timeout

    def interrupt_turn(self) -> None:
        if not self.thread_id or not self.active_turn_id:
            return
        request_id = self.next_id("turn-interrupt")
        params = {"threadId": self.thread_id, "turnId": self.active_turn_id}
        self.write(json_rpc_request(request_id, "turn/interrupt", params))
        self.active_turn_id = ""

    def handle_async_message(self, message: dict[str, Any], deadline: float) -> bool:
        now = time.monotonic()
        metrics = self.current_metrics
        self.last_normalized_events = normalize_codex_app_server_message(message)
        if message.get("id") is not None and message.get("method"):
            if metrics is not None:
                metrics.mark_server_message(str(message.get("method") or ""), now)
                collect_token_usage(message, metrics)
            self.handle_server_request(message, deadline)
            return False
        method = str(message.get("method") or "")
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        if metrics is not None:
            metrics.mark_server_message(method, now)
            collect_token_usage(message, metrics)
        if method == "warning":
            warning = human_error_message(params.get("message") or params.get("warning") or params.get("error") or params)
            if warning:
                self.print_aux_stderr(f"codex app-server warning: {warning}")
            return False
        if method == "error":
            error = human_error_message(params.get("error") or params.get("message") or params)
            if error.casefold().startswith("reconnecting"):
                self.print_aux_stderr(f"codex app-server warning: {error}")
                return False
            self.turn_error_message = error or "codex app-server error"
            self.turn_error_printed = True
            self.finish_prefixed_output()
            self.finish_answer_output()
            self.print_aux_stderr(f"codex app-server error: {self.turn_error_message}")
            self.active_turn_id = ""
            return True
        if method == "turn/started":
            if str(params.get("threadId") or "") == self.thread_id:
                self.active_turn_id = turn_id_from_turn(params.get("turn")) or self.active_turn_id
                if metrics is not None and not metrics.turn_started_at:
                    metrics.turn_started_at = now
            return False
        if method == "item/agentMessage/delta":
            delta = str(params.get("delta") or "")
            if delta:
                if metrics is not None:
                    metrics.record_answer_text(delta, now)
                self.finish_prefixed_output()
                self.answer_buffer.append(delta)
                print(delta, end="", flush=True)
                self.answer_output_at_line_start = delta.endswith(("\n", "\r"))
            return False
        if method == "item/reasoning/summaryTextDelta":
            delta = str(params.get("delta") or "")
            if delta:
                if metrics is not None:
                    metrics.record_reasoning_summary(delta, now)
                self.reasoning_summary_buffer.append(delta)
                if self.args.show_reasoning_summary:
                    self.write_prefixed_stdout(CODEX_OUTPUT_TERMS.prefix, delta)
            return False
        if method == "item/reasoning/summaryPartAdded":
            return False
        if method == "item/reasoning/textDelta":
            delta = str(params.get("delta") or "")
            if delta:
                if metrics is not None:
                    metrics.record_raw_reasoning(delta, now)
                self.raw_reasoning_buffer.append(delta)
                if self.args.show_raw_reasoning:
                    self.write_prefixed_stdout(CODEX_OUTPUT_TERMS.prefix, delta)
            return False
        if method in {"item/reasoning/delta", "item/thinking/delta", "item/thought/delta"}:
            if metrics is not None and not metrics.first_reasoning_at:
                metrics.first_reasoning_at = now
            if self.args.show_reasoning_summary or self.args.show_raw_reasoning:
                self.write_prefixed_line(CODEX_OUTPUT_TERMS.prefix, "event")
            return False
        if method in {"item/commandExecution/outputDelta", "item/fileChange/outputDelta"}:
            delta = str(params.get("delta") or "")
            if delta and metrics is not None:
                metrics.record_tool_output(delta, now)
            item_id = str(params.get("itemId") or "").strip()
            if item_id:
                self.tool_output_item_ids.add(item_id)
            if self.args.show_tool_output:
                if delta:
                    self.write_prefixed_stdout(TOOL_OUTPUT_PREFIX, delta)
            return False
        if method == "item/commandExecution/terminalInteraction":
            if metrics is not None and not metrics.first_tool_at:
                metrics.first_tool_at = now
            if self.args.show_tool_output:
                self.write_prefixed_line(TOOL_OUTPUT_PREFIX, "terminal interaction")
            return False
        if method == "item/mcpToolCall/progress":
            if metrics is not None and not metrics.first_tool_at:
                metrics.first_tool_at = now
            if self.args.show_tool_output:
                progress = str(params.get("message") or "").strip()
                if progress:
                    self.write_prefixed_line(TOOL_OUTPUT_PREFIX, progress)
            return False
        if method == "item/started":
            item = params.get("item") if isinstance(params.get("item"), dict) else {}
            if metrics is not None:
                metrics.record_tool_item("start", item, now)
            self.emit_tool_item("start", item)
            return False
        if method == "item/completed":
            item = params.get("item") if isinstance(params.get("item"), dict) else {}
            if metrics is not None:
                metrics.record_tool_item("done", item, now)
                if item.get("type") == "commandExecution":
                    item_id = str(item.get("id") or "").strip()
                    aggregated_output = str(item.get("aggregatedOutput") or "")
                    if aggregated_output and item_id not in self.tool_output_item_ids:
                        metrics.record_tool_output(aggregated_output, now)
            if item.get("type") == "agentMessage" and str(item.get("phase") or "") == "final_answer":
                self.final_item_text = str(item.get("text") or "").strip()
                if metrics is not None and self.final_item_text and not self.answer_buffer and metrics.answer_chars == 0:
                    metrics.record_answer_text(self.final_item_text, now)
                self.finish_visible_answer()
                self.active_turn_id = ""
                return True
            self.emit_tool_item("done", item)
            return False
        if method == "thread/status/changed":
            status = params.get("status") if isinstance(params.get("status"), dict) else {}
            if str(params.get("threadId") or "") == self.thread_id and status.get("type") == "idle" and (self.answer_buffer or self.final_item_text):
                self.finish_visible_answer()
                self.active_turn_id = ""
                return True
            return False
        if method == "turn/completed":
            if str(params.get("threadId") or "") != self.thread_id:
                return False
            turn = params.get("turn")
            turn_error = turn_error_message(turn)
            if turn_error:
                self.turn_error_message = turn_error
                if not self.turn_error_printed:
                    self.turn_error_printed = True
                    self.finish_prefixed_output()
                    self.finish_answer_output()
                    self.print_aux_stderr(f"codex app-server error: {turn_error}")
            final_text = turn_text(turn)
            if final_text:
                self.final_item_text = final_text
                if metrics is not None and not self.answer_buffer and metrics.answer_chars == 0:
                    metrics.record_answer_text(final_text, now)
            self.finish_visible_answer()
            self.active_turn_id = ""
            return True
        return False

    def finish_visible_answer(self) -> None:
        self.finish_prefixed_output()
        if self.final_item_text and not self.answer_buffer:
            print(self.final_item_text, end="", flush=True)
            self.answer_output_at_line_start = self.final_item_text.endswith(("\n", "\r"))
        if (self.answer_buffer or self.final_item_text) and not self.answer_output_at_line_start:
            print("", flush=True)
            self.answer_output_at_line_start = True
        if self.reasoning_summary_buffer and not self.args.show_reasoning_summary:
            self.print_aux_stderr(f"[{CODEX_OUTPUT_TERMS.lower_label} summary received; rerun with -c {CODEX_CONFIG_KEYS.hidden_work_summary}=true to display it]")
        if self.raw_reasoning_buffer and not self.args.show_raw_reasoning:
            self.print_aux_stderr(f"[raw {CODEX_OUTPUT_TERMS.lower_label} received but hidden; rerun with -c {CODEX_CONFIG_KEYS.hidden_work_raw}=true to display it]")

    def handle_server_request(self, message: dict[str, Any], deadline: float) -> None:
        del deadline
        request_id = message.get("id")
        method = str(message.get("method") or "")
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        if method not in APPROVAL_METHODS:
            self.write_prefixed_line(TOOL_OUTPUT_PREFIX, f"unsupported server request: {method}")
            self.write(json_rpc_error(request_id, f"unsupported server request: {method}"))
            return
        if self.current_metrics is not None:
            self.current_metrics.approval_requests += 1
        decision = self.approval_decision(method, params)
        self.write(json_rpc_response(request_id, {"decision": decision}))

    def approval_decision(self, method: str, params: dict[str, Any]) -> str:
        self.write_prefixed_line(TOOL_OUTPUT_PREFIX, "approval requested")
        self.write_prefixed_line(TOOL_OUTPUT_PREFIX, f"method: {method}")
        reason = str(params.get("reason") or "").strip()
        if reason:
            self.write_prefixed_line(TOOL_OUTPUT_PREFIX, f"reason: {reason}")
        command = shell_command_text(params.get("command"))
        if command:
            self.write_prefixed_line(TOOL_OUTPUT_PREFIX, f"command: {command}")
        cwd = str(params.get("cwd") or "").strip()
        if cwd:
            self.write_prefixed_line(TOOL_OUTPUT_PREFIX, f"cwd: {cwd}")
        file_changes = params.get("fileChanges")
        if isinstance(file_changes, dict):
            self.write_prefixed_line(TOOL_OUTPUT_PREFIX, "files:")
            for path in file_changes:
                self.write_prefixed_line(TOOL_OUTPUT_PREFIX, f"  {path}")
        mode = self.args.approval_mode
        if mode == "prompt" and not sys.stdin.isatty():
            mode = "deny"
        if mode == "prompt":
            choice = input("Approve? [y]es/[n]o/[s]ession/[a]bort: ").strip().lower()
            if choice.startswith("y"):
                mode = "accept"
            elif choice.startswith("s"):
                mode = "accept-session"
            elif choice.startswith("a"):
                mode = "abort"
            else:
                mode = "deny"
        if method in {"item/commandExecution/requestApproval", "item/fileChange/requestApproval"}:
            if mode == "accept":
                return "accept"
            if mode == "accept-session":
                return "acceptForSession"
            if mode == "abort":
                return "cancel"
            return "decline"
        if mode == "accept":
            return "approved"
        if mode == "accept-session":
            return "approved_for_session"
        if mode == "abort":
            return "abort"
        return "denied"


def parse_args() -> argparse.Namespace:
    help_epilog = build_config_help()
    if any(arg in {"-h", "--help"} for arg in sys.argv[1:]):
        model_rows, catalog_error = fetch_help_model_catalog()
        help_epilog = build_config_help(model_rows, catalog_error)
    parser = argparse.ArgumentParser(
        description="Codex text client prototype. Accepts the interactive Codex CLI flags it can map.",
        epilog=help_epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("prompt", nargs="*", help="Optional user prompt to start the session.")
    parser.add_argument("-c", "--config", action="append", default=[], metavar="key=value", help="Override a Codex config value, using the same syntax as codex -c.")
    parser.add_argument("--enable", action="append", default=[], metavar="FEATURE", help="Enable a feature, equivalent to -c features.<name>=true.")
    parser.add_argument("--disable", action="append", default=[], metavar="FEATURE", help="Disable a feature, equivalent to -c features.<name>=false.")
    parser.add_argument("--remote", default="", metavar="ADDR", help="Accepted for Codex CLI compatibility; remote websocket mode is not implemented by this prototype.")
    parser.add_argument("--remote-auth-token-env", default="", metavar="TOKEN_VAR", help="Accepted for Codex CLI compatibility.")
    parser.add_argument("--strict-config", action="store_true", help="Error out when config contains fields not recognized by this prototype.")
    parser.add_argument("-i", "--image", action="append", default=[], metavar="FILE", help="Accepted for Codex CLI compatibility; image input is not implemented by this prototype.")
    parser.add_argument("-m", "--model", default="", metavar="MODEL", help="Model the agent should use.")
    parser.add_argument("--oss", action="store_true", help="Accepted for Codex CLI compatibility; local OSS provider mode is not implemented by this prototype.")
    parser.add_argument("--local-provider", default="", metavar="OSS_PROVIDER", help="Accepted for Codex CLI compatibility.")
    parser.add_argument("-p", "--profile", default="", metavar="CONFIG_PROFILE_V2", help="Accepted for Codex CLI compatibility.")
    parser.add_argument("-s", "--sandbox", choices=sorted(SANDBOX_MODES), default=DEFAULT_SANDBOX, help=f"Select the sandbox policy to use. Default: {DEFAULT_SANDBOX}.")
    parser.add_argument(CODEX_BYPASS_APPROVALS_FLAG, action="store_true", help="Skip approval prompts and run without sandboxing.")
    parser.add_argument(CODEX_BYPASS_HOOK_TRUST_FLAG, action="store_true", default=DEFAULT_BYPASS_HOOK_TRUST, help="Run enabled hooks without persisted hook trust. Default: on for this client.")
    parser.add_argument("-C", "--cd", dest="cwd", default=os.getcwd(), metavar="DIR", help="Tell the agent to use the specified directory as its working root.")
    parser.add_argument("--add-dir", action="append", default=[], metavar="DIR", help="Additional writable directories; accepted for Codex CLI compatibility.")
    parser.add_argument("-a", "--ask-for-approval", dest="approval_policy", choices=sorted(APPROVAL_POLICIES), default=DEFAULT_APPROVAL_POLICY, metavar="APPROVAL_POLICY", help=f"Configure when Codex requires human approval. Default: {DEFAULT_APPROVAL_POLICY}.")
    parser.add_argument("--search", action="store_true", help="Enable live web search.")
    parser.add_argument("--no-alt-screen", action="store_true", help="Accepted for Codex CLI compatibility.")
    parser.add_argument("-V", "--version", action="version", version=f"codex-text-client {CLIENT_VERSION}")
    args = parser.parse_args()
    args.approval_mode = DEFAULT_TEXT_CLIENT_APPROVAL_MODE
    args.debug_json = False
    args.effort = ""
    args.ephemeral = False
    args.include_hidden_models = False
    args.interactive = False
    args.list_models = False
    args.reasoning_summary = "concise"
    args.show_raw_reasoning = False
    args.show_metrics = False
    args.show_reasoning_summary = True
    args.show_tool_output = True
    args.thread_id = ""
    args.timeout = APP_SERVER_TIMEOUT_SECONDS
    args.base_instructions = ""
    args.service_tier = ""
    args.raw_output = True
    args.config_values = {}
    for feature in args.enable:
        args.config_values[f"features.{feature}"] = True
    for feature in args.disable:
        args.config_values[f"features.{feature}"] = False
    for item in args.config:
        key, separator, raw_value = item.partition("=")
        if not separator:
            parser.error(f"invalid -c/--config override {item!r}; expected key=value")
        if args.strict_config and key not in KNOWN_CONFIG_KEYS and not key.startswith("features."):
            parser.error(f"unrecognized config key: {key}")
        value = parse_config_value(raw_value)
        apply_config_override(args, key, value, parser)
        args.config_values[key] = normalized_config_value(key, value)
    if args.dangerously_bypass_approvals_and_sandbox:
        args.sandbox = DEFAULT_SANDBOX
        args.approval_policy = DEFAULT_APPROVAL_POLICY
    if args.search:
        args.config_values["web_search"] = "live"
    args.cwd = str(Path(args.cwd).expanduser().resolve())
    args.exec_mode = False
    if args.prompt and args.prompt[0] in {"exec", "e"}:
        args.exec_mode = True
        args.prompt = args.prompt[1:]
    if args.prompt and args.prompt[0] == "resume":
        if len(args.prompt) < 2:
            parser.error("resume requires a thread id")
        args.thread_id = args.prompt[1]
        args.prompt = args.prompt[2:]
    return args


def normalized_config_value(key: str, value: Any) -> Any:
    if key == "model_reasoning_summary":
        return normalize_reasoning_summary(value)
    if key == CODEX_CONFIG_KEYS.effort:
        return str(value).strip().lower()
    if key in {"approval_policy", "sandbox", "sandbox_mode", "service_tier", CODEX_CONFIG_KEYS.session, "text_client.approval_mode", CODEX_CONFIG_KEYS.model}:
        return str(value)
    if key == "bypass_hook_trust":
        return parse_config_bool(value)
    if key in {
        CODEX_CONFIG_KEYS.hidden_work_summary,
        CODEX_CONFIG_KEYS.hidden_work_raw,
        CODEX_CONFIG_KEYS.tool_output,
        "text_client.include_hidden_models",
        CODEX_CONFIG_KEYS.raw_output,
        "text_client.debug_json",
        CODEX_CONFIG_KEYS.metrics,
    }:
        return parse_config_bool(value)
    if key == CODEX_CONFIG_KEYS.timeout:
        return float(value)
    if key == "web_search":
        if value == "live":
            return "live"
        return parse_config_bool(value)
    return value


def set_config_override(args: argparse.Namespace, key: str, value: Any) -> None:
    if key == CODEX_CONFIG_KEYS.model:
        args.model = str(value)
        return
    if key == CODEX_CONFIG_KEYS.effort:
        effort = str(value).strip().lower()
        if effort not in REASONING_EFFORTS:
            raise ValueError(f"{CODEX_CONFIG_KEYS.effort}, the {CODEX_OUTPUT_TERMS.lower_label} effort setting, must be one of: {', '.join(sorted(REASONING_EFFORTS))}")
        args.effort = effort
        return
    if key == "model_reasoning_summary":
        args.reasoning_summary = normalize_reasoning_summary(value)
        return
    if key == "approval_policy":
        approval_policy = str(value)
        if approval_policy not in APPROVAL_POLICIES:
            raise ValueError(f"approval_policy must be one of: {', '.join(sorted(APPROVAL_POLICIES))}")
        args.approval_policy = approval_policy
        return
    if key == "bypass_hook_trust":
        args.dangerously_bypass_hook_trust = parse_config_bool(value)
        return
    if key in {"sandbox", "sandbox_mode"}:
        sandbox = str(value)
        if sandbox not in SANDBOX_MODES:
            raise ValueError(f"{key} must be one of: {', '.join(sorted(SANDBOX_MODES))}")
        args.sandbox = sandbox
        return
    if key == "service_tier":
        args.service_tier = str(value)
        return
    if key == "web_search":
        args.search = value == "live" or parse_config_bool(value)
        return
    if key == CODEX_CONFIG_KEYS.hidden_work_summary:
        args.show_reasoning_summary = parse_config_bool(value)
        return
    if key == CODEX_CONFIG_KEYS.hidden_work_raw:
        args.show_raw_reasoning = parse_config_bool(value)
        return
    if key == CODEX_CONFIG_KEYS.tool_output:
        args.show_tool_output = parse_config_bool(value)
        return
    if key == CODEX_CONFIG_KEYS.metrics:
        args.show_metrics = parse_config_bool(value)
        return
    if key == CODEX_CONFIG_KEYS.timeout:
        args.timeout = float(value)
        return
    if key == CODEX_CONFIG_KEYS.session:
        args.thread_id = str(value)
        return
    if key == "text_client.approval_mode":
        approval_mode = str(value)
        if approval_mode not in {"prompt", "accept", "accept-session", "deny", "abort"}:
            raise ValueError("text_client.approval_mode must be one of: prompt, accept, accept-session, deny, abort")
        args.approval_mode = approval_mode
        return
    if key == "text_client.include_hidden_models":
        args.include_hidden_models = parse_config_bool(value)
        return
    if key == CODEX_CONFIG_KEYS.raw_output:
        args.raw_output = parse_config_bool(value)
        return
    if key == "text_client.debug_json":
        args.debug_json = parse_config_bool(value)


def apply_config_override(args: argparse.Namespace, key: str, value: Any, parser: argparse.ArgumentParser) -> None:
    try:
        set_config_override(args, key, value)
    except ValueError as exc:
        parser.error(str(exc))


def main() -> int:
    args = parse_args()
    client = CodexTextClient(args)
    try:
        client.start()
        if args.list_models:
            client.list_models()
            return 0
        initial_prompt = " ".join(args.prompt).strip()
        if initial_prompt:
            ok = client.send_turn(initial_prompt)
            if not args.interactive:
                return 0 if ok else 1
        configure_readline(REPL_COMMANDS)
        prompt_session = PromptInputSession(REPL_COMMANDS)
        print(f"Codex text client. Type /quit to exit.\n  session: {client.thread_id or 'new'} jsonl: {client.session_file_path()}", file=sys.stderr)
        while True:
            try:
                text = prompt_session.read(client.prompt_text())
            except EOFError:
                print("", file=sys.stderr)
                return 0
            if text.strip() in {"/q", "/quit", "quit", "exit"}:
                return 0
            if text.strip().startswith("/"):
                try:
                    result = client.handle_repl_command(text.strip())
                except TimeoutError as exc:
                    client.restart_after_timeout(exc)
                    continue
                if result == "quit":
                    return 0
                continue
            client.send_turn(text)
    except TimeoutError as exc:
        client.print_aux_stderr(f"codex app-server timeout: {exc}")
        return 1
    except (RuntimeError, OSError, json.JSONDecodeError) as exc:
        client.print_aux_stderr(f"codex app-server error: {exc}")
        return 1
    except KeyboardInterrupt:
        client.print_aux_stderr("\ninterrupted")
        return 130
    finally:
        client.print_exit_hint()
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
