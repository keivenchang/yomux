#!/usr/bin/env python3
"""Text prototype client for `codex app-server`.

This intentionally talks to the structured JSON-RPC app-server instead of scraping a
visible Codex TUI. It is a prototype for the kind of client YOLOmux can own directly:
start/resume a Codex thread, send turns, stream answer deltas, optionally display
reasoning summaries/raw reasoning events when the server emits them, and relay basic
approval prompts.

Usage:
  python3 prototypes/codex_text_client.py
  python3 prototypes/codex_text_client.py --cwd . "summarize this repo"
  python3 prototypes/codex_text_client.py --show-reasoning-summary "think briefly, then answer"
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
from typing import Any


CLIENT_VERSION = "prototype"
APP_SERVER_TIMEOUT_SECONDS = 300.0
APPROVAL_METHODS = {
    "item/commandExecution/requestApproval",
    "item/fileChange/requestApproval",
    "applyPatchApproval",
    "execCommandApproval",
}


def json_rpc_request(request_id: str, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        payload["params"] = params
    return payload


def json_rpc_notification(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        payload["params"] = params
    return payload


def json_rpc_response(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def json_rpc_error(request_id: Any, message: str, code: int = -32601) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


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


def app_server_env() -> dict[str, str]:
    env = dict(os.environ)
    local_bin = str(Path.home() / ".local" / "bin")
    path_entries = env.get("PATH", "").split(os.pathsep) if env.get("PATH") else []
    if local_bin not in path_entries:
        env["PATH"] = os.pathsep.join([local_bin, *path_entries]) if path_entries else local_bin
    env["TERM"] = "xterm-256color"
    env["NO_COLOR"] = "1"
    return env


class CodexTextClient:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.process: subprocess.Popen[str] | None = None
        self.request_counter = 0
        self.thread_id = str(args.thread_id or "").strip()
        self.answer_buffer: list[str] = []
        self.reasoning_summary_buffer: list[str] = []
        self.raw_reasoning_buffer: list[str] = []
        self.final_item_text = ""

    def next_id(self, prefix: str) -> str:
        self.request_counter += 1
        return f"{prefix}-{self.request_counter}"

    def start(self) -> None:
        codex_path = shutil.which("codex")
        if not codex_path:
            raise RuntimeError("codex CLI not found on PATH")
        command = [codex_path, "app-server", "--listen", "stdio://"]
        if self.args.effort:
            command.extend(["-c", f'model_reasoning_effort="{self.args.effort}"'])
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
        if self.args.debug_json:
            print(f"[initialized] {json.dumps(response, sort_keys=True)}", file=sys.stderr)

    def close(self) -> None:
        if self.process is None:
            return
        process = self.process
        self.process = None
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)

    def write(self, message: dict[str, Any]) -> None:
        if self.process is None or self.process.stdin is None:
            raise RuntimeError("codex app-server is not running")
        self.process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
        self.process.stdin.flush()
        if self.args.debug_json:
            print(f"> {json.dumps(message, sort_keys=True)}", file=sys.stderr)

    def readline(self, deadline: float) -> str:
        if self.process is None or self.process.stdout is None:
            raise RuntimeError("codex app-server is not running")
        timeout = max(0.0, deadline - time.monotonic())
        with selectors.DefaultSelector() as selector:
            selector.register(self.process.stdout, selectors.EVENT_READ)
            if not selector.select(timeout):
                raise TimeoutError("timed out waiting for codex app-server")
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError("codex app-server exited without a JSON-RPC message")
        return line

    def read_message(self, deadline: float) -> dict[str, Any]:
        line = self.readline(deadline)
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"codex app-server emitted invalid JSON-RPC: {exc}") from exc
        if not isinstance(message, dict):
            raise RuntimeError("codex app-server emitted a non-object JSON-RPC message")
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
                    raise RuntimeError(f"{method} failed: {message.get('error')}")
                result = message.get("result")
                return result if isinstance(result, dict) else {}
            self.handle_async_message(message, deadline)

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
        print(f"[thread] {self.thread_id}", file=sys.stderr)
        return self.thread_id

    def thread_params(self) -> dict[str, Any]:
        params: dict[str, Any] = {
            "approvalPolicy": self.args.approval_policy,
            "approvalsReviewer": "user",
            "cwd": str(Path(self.args.cwd).expanduser().resolve()),
            "ephemeral": bool(self.args.ephemeral),
            "sandbox": self.args.sandbox,
        }
        if self.args.model:
            params["model"] = self.args.model
        if self.args.base_instructions:
            params["baseInstructions"] = self.args.base_instructions
        return params

    def turn_params(self, text: str) -> dict[str, Any]:
        summary = self.args.reasoning_summary
        if self.args.show_reasoning_summary and summary == "none":
            summary = "auto"
        params: dict[str, Any] = {
            "threadId": self.thread_id,
            "input": [{"type": "text", "text": text, "text_elements": []}],
            "cwd": str(Path(self.args.cwd).expanduser().resolve()),
            "summary": summary,
        }
        if self.args.effort:
            params["effort"] = self.args.effort
        if self.args.model:
            params["model"] = self.args.model
        return params

    def send_turn(self, text: str) -> None:
        if not text.strip():
            return
        self.ensure_thread()
        self.answer_buffer = []
        self.reasoning_summary_buffer = []
        self.raw_reasoning_buffer = []
        self.final_item_text = ""
        turn_response = self.request("turn/start", self.turn_params(text))
        if self.args.debug_json:
            print(f"[turn] {json.dumps(turn_response, sort_keys=True)}", file=sys.stderr)
        self.wait_for_turn()

    def wait_for_turn(self) -> None:
        deadline = time.monotonic() + self.args.timeout
        while True:
            message = self.read_message(deadline)
            if self.handle_async_message(message, deadline):
                return

    def handle_async_message(self, message: dict[str, Any], deadline: float) -> bool:
        if message.get("id") is not None and message.get("method"):
            self.handle_server_request(message, deadline)
            return False
        method = str(message.get("method") or "")
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        if method == "item/agentMessage/delta":
            delta = str(params.get("delta") or "")
            if delta:
                self.answer_buffer.append(delta)
                print(delta, end="", flush=True)
            return False
        if method == "item/reasoning/summaryTextDelta":
            delta = str(params.get("delta") or "")
            if delta:
                self.reasoning_summary_buffer.append(delta)
                if self.args.show_reasoning_summary:
                    print(delta, end="", file=sys.stderr, flush=True)
            return False
        if method == "item/reasoning/summaryPartAdded":
            if self.args.show_reasoning_summary:
                print("\n[reasoning-summary]", file=sys.stderr, flush=True)
            return False
        if method == "item/reasoning/textDelta":
            delta = str(params.get("delta") or "")
            if delta:
                self.raw_reasoning_buffer.append(delta)
                if self.args.show_raw_reasoning:
                    print(delta, end="", file=sys.stderr, flush=True)
            return False
        if method in {"item/reasoning/delta", "item/thinking/delta", "item/thought/delta"}:
            if self.args.show_reasoning_summary or self.args.show_raw_reasoning:
                print("[reasoning event]", file=sys.stderr)
            return False
        if method == "item/commandExecution/outputDelta":
            if self.args.show_tool_output:
                delta = str(params.get("delta") or "")
                if delta:
                    print(delta, end="", file=sys.stderr, flush=True)
            return False
        if method == "item/completed":
            item = params.get("item") if isinstance(params.get("item"), dict) else {}
            if item.get("type") == "agentMessage" and str(item.get("phase") or "") == "final_answer":
                self.final_item_text = str(item.get("text") or "").strip()
                self.finish_visible_answer()
                return True
            return False
        if method == "thread/status/changed":
            status = params.get("status") if isinstance(params.get("status"), dict) else {}
            if str(params.get("threadId") or "") == self.thread_id and status.get("type") == "idle" and (self.answer_buffer or self.final_item_text):
                self.finish_visible_answer()
                return True
            return False
        if method == "turn/completed":
            if str(params.get("threadId") or "") != self.thread_id:
                return False
            final_text = turn_text(params.get("turn"))
            if final_text:
                self.final_item_text = final_text
            self.finish_visible_answer()
            return True
        return False

    def finish_visible_answer(self) -> None:
        if self.final_item_text and not self.answer_buffer:
            print(self.final_item_text, end="", flush=True)
        if self.answer_buffer or self.final_item_text:
            print("", flush=True)
        if self.reasoning_summary_buffer and not self.args.show_reasoning_summary:
            print("[reasoning summary received; rerun with --show-reasoning-summary to display it]", file=sys.stderr)
        if self.raw_reasoning_buffer and not self.args.show_raw_reasoning:
            print("[raw reasoning received but hidden; rerun with --show-raw-reasoning to display it]", file=sys.stderr)

    def handle_server_request(self, message: dict[str, Any], deadline: float) -> None:
        del deadline
        request_id = message.get("id")
        method = str(message.get("method") or "")
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        if method not in APPROVAL_METHODS:
            print(f"[unsupported server request] {method}", file=sys.stderr)
            self.write(json_rpc_error(request_id, f"unsupported server request: {method}"))
            return
        decision = self.approval_decision(method, params)
        self.write(json_rpc_response(request_id, {"decision": decision}))

    def approval_decision(self, method: str, params: dict[str, Any]) -> str:
        print("\n[approval requested]", file=sys.stderr)
        print(f"method: {method}", file=sys.stderr)
        reason = str(params.get("reason") or "").strip()
        if reason:
            print(f"reason: {reason}", file=sys.stderr)
        command = shell_command_text(params.get("command"))
        if command:
            print(f"command: {command}", file=sys.stderr)
        cwd = str(params.get("cwd") or "").strip()
        if cwd:
            print(f"cwd: {cwd}", file=sys.stderr)
        file_changes = params.get("fileChanges")
        if isinstance(file_changes, dict):
            print("files:", file=sys.stderr)
            for path in file_changes:
                print(f"  {path}", file=sys.stderr)
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
    parser = argparse.ArgumentParser(description="Prototype text client for codex app-server JSON-RPC.")
    parser.add_argument("prompt", nargs="*", help="Optional one-shot prompt. Omit for interactive mode.")
    parser.add_argument("--cwd", default=os.getcwd(), help="Working directory for the Codex thread.")
    parser.add_argument("--model", default="", help="Optional model override.")
    parser.add_argument("--effort", default="", help="Optional reasoning effort override, e.g. low/medium/high/xhigh.")
    parser.add_argument("--reasoning-summary", choices=["none", "auto", "concise", "detailed"], default="none", help="Request Codex reasoning summaries.")
    parser.add_argument("--show-reasoning-summary", action="store_true", help="Print reasoning summary deltas to stderr.")
    parser.add_argument("--show-raw-reasoning", action="store_true", help="Print raw reasoning text deltas if the app-server emits them.")
    parser.add_argument("--show-tool-output", action="store_true", help="Print command output deltas to stderr.")
    parser.add_argument("--sandbox", choices=["read-only", "workspace-write", "danger-full-access"], default="read-only", help="Thread sandbox mode.")
    parser.add_argument("--approval-policy", choices=["untrusted", "on-request", "never"], default="on-request", help="When Codex should request approval.")
    parser.add_argument("--approval-mode", choices=["prompt", "accept", "accept-session", "deny", "abort"], default="prompt", help="How this client answers approval requests.")
    parser.add_argument("--thread-id", default="", help="Resume an existing app-server thread id.")
    parser.add_argument("--ephemeral", action="store_true", help="Request an ephemeral thread.")
    parser.add_argument("--base-instructions", default="", help="Optional base instructions for the thread.")
    parser.add_argument("--service-tier", default="", help="Optional service tier override passed to Codex config.")
    parser.add_argument("--timeout", type=float, default=APP_SERVER_TIMEOUT_SECONDS, help="Per-request/turn timeout in seconds.")
    parser.add_argument("--debug-json", action="store_true", help="Print JSON-RPC traffic to stderr.")
    parser.add_argument("--interactive", action="store_true", help="Continue as a REPL after a one-shot prompt.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    client = CodexTextClient(args)
    try:
        client.start()
        initial_prompt = " ".join(args.prompt).strip()
        if initial_prompt:
            client.send_turn(initial_prompt)
            if not args.interactive:
                return 0
        print("Codex text client. Type /quit to exit.", file=sys.stderr)
        while True:
            try:
                text = input("codex> ")
            except EOFError:
                print("", file=sys.stderr)
                return 0
            if text.strip() in {"/q", "/quit", "quit", "exit"}:
                return 0
            client.send_turn(text)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
