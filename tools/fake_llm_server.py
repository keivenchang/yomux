#!/usr/bin/env python3
"""Tiny local OpenAI/Anthropic-compatible response server for agent E2E tests."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


OPENAI_MODEL = "gpt-4o-mini"
ANTHROPIC_MODEL = "claude-3-5-haiku-20241022"
_LOG_LOCK = threading.Lock()


def _request_timestamp() -> float:
    """Return a reproducible request timestamp when the harness supplies one."""
    start = os.environ.get("FAKE_LLM_TIMESTAMP_START")
    step = os.environ.get("FAKE_LLM_TIMESTAMP_STEP")
    if start is None or step is None:
        return time.time()
    try:
        value = float(start)
        increment = float(step)
    except ValueError:
        return time.time()
    with _LOG_LOCK:
        sequence = int(os.environ.get("FAKE_LLM_TIMESTAMP_SEQUENCE", "0"))
        os.environ["FAKE_LLM_TIMESTAMP_SEQUENCE"] = str(sequence + 1)
    return value + sequence * increment


def _response_usage(payload):
    usage = payload.get("usage") if isinstance(payload, dict) else None
    if isinstance(usage, dict):
        return dict(usage)
    return {}


def _opencode_usage():
    """Usage fields accepted by OpenCode's OpenAI-compatible provider adapter."""
    return {
        "prompt_tokens": 12,
        "completion_tokens": 8,
        "total_tokens": 20,
        "prompt_tokens_details": {"cached_tokens": 2},
    }


def _scenario_from_path(path: str) -> str:
    parts = path.split("/scenario/", 1)
    if len(parts) != 2:
        return "normal"
    return parts[1].split("/", 1)[0] or "normal"


def _log_request(body, *, path, scenario, model, provider, usage):
    log_path = os.environ.get("FAKE_LLM_LOG")
    if not log_path:
        return
    try:
        request = json.loads(body or b"{}")
    except json.JSONDecodeError:
        request = {}
    record = {
        "timestamp": _request_timestamp(),
        "path": path,
        "scenario": scenario,
        "model": model,
        "provider": provider,
        "usage": usage,
        "request": request,
    }
    with open(log_path, "a", encoding="utf-8") as log:
        log.write(json.dumps(record, sort_keys=True) + "\n")


def _log_event(body, *, path, scenario, model, provider, phase, response_kind=""):
    """Persist provider lifecycle events, including requests still in flight."""
    log_path = os.environ.get("FAKE_LLM_EVENT_LOG")
    if not log_path:
        return
    try:
        request = json.loads(body or b"{}")
    except json.JSONDecodeError:
        request = {}
    record = {
        "timestamp": _request_timestamp(),
        "phase": phase,
        "path": path,
        "scenario": scenario,
        "model": model,
        "provider": provider,
        "response_kind": response_kind,
        "request": request,
    }
    with open(log_path, "a", encoding="utf-8") as log:
        log.write(json.dumps(record, sort_keys=True) + "\n")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self):  # noqa: N802 - stdlib handler API
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        try:
            request = json.loads(body or b"{}")
        except json.JSONDecodeError:
            request = {}
        # URL identity is authoritative; the environment may only alter behavior.
        scenario = _scenario_from_path(self.path)
        behavior = os.environ.get("FAKE_LLM_SCENARIO", scenario)
        anthropic = "anthropic" in self.path or self.headers.get("Anthropic-Version")
        responses = self.path.endswith("/responses")
        response_text = os.environ.get("FAKE_LLM_RESPONSE_TEXT", "YOLOMUX_FAKE_OK")
        provider = "anthropic" if anthropic else "openai"
        _log_event(
            body,
            path=self.path,
            scenario=scenario,
            model=ANTHROPIC_MODEL if anthropic else OPENAI_MODEL,
            provider=provider,
            phase="started",
        )
        # Record admission before a held request sleeps so active-state evidence has a
        # durable request record while the provider call is still in flight.
        if behavior in {"hold", "delay"}:
            if anthropic:
                request_usage = {"input_tokens": 12, "output_tokens": 8}
            elif responses:
                request_usage = _opencode_usage()
            else:
                request_usage = _opencode_usage()
            _log_request(body, path=self.path, scenario=scenario, model=ANTHROPIC_MODEL if anthropic else OPENAI_MODEL, provider=provider, usage=request_usage)
        if behavior == "disconnect":
            _log_request(body, path=self.path, scenario=scenario, model=OPENAI_MODEL, provider="openai", usage={})
            self.close_connection = True
            return
        if behavior == "delay":
            time.sleep(min(float(os.environ.get("FAKE_LLM_DELAY", "0.1")), 30))
        if behavior == "hold":
            time.sleep(min(float(os.environ.get("FAKE_LLM_HOLD", "5")), 60))
        if behavior == "error":
            self.send_response(502)
            payload = {"error": {"type": "fake_llm_error", "message": "deterministic fake provider error"}}
        elif anthropic:
            tool = behavior in {"tool", "approval"}
            content = [{"type": "tool_use", "id": "toolu_fake_1", "name": "Bash", "input": {"command": "echo YOLOMUX_FAKE_OK"}}] if tool else [{"type": "text", "text": response_text}]
            payload = {"id": "msg_yolomux", "type": "message", "role": "assistant", "content": content, "model": ANTHROPIC_MODEL, "stop_reason": "tool_use" if tool else "end_turn", "stop_sequence": None, "usage": {"input_tokens": 12, "output_tokens": 8}}
        elif responses:
            tool = behavior in {"tool", "approval"}
            payload = {
                "id": "resp_yolomux",
                "object": "response",
                "status": "completed",
                "model": OPENAI_MODEL,
                "output": ([{"type": "function_call", "call_id": "call_fake_1", "name": "exec_command", "arguments": '{"cmd":"echo YOLOMUX_FAKE_OK"}'}] if tool else [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": response_text}]}]),
                "usage": _opencode_usage(),
            }
        else:
            tool = behavior in {"tool", "approval"}
            message = {"role": "assistant", "content": None if tool else response_text}
            if tool:
                message["tool_calls"] = [{"id": "call_fake_1", "type": "function", "function": {"name": "Bash", "arguments": '{"command":"echo YOLOMUX_FAKE_OK"}'}}]
            payload = {"id": "chatcmpl_yolomux", "object": "chat.completion", "created": 1, "system_fingerprint": "yolomux-fake", "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if tool else "stop"}], "model": OPENAI_MODEL, "usage": _opencode_usage()}
        response_kind = "tool" if (
            (anthropic and any(item.get("type") == "tool_use" for item in payload.get("content", []) if isinstance(item, dict)))
            or any(item.get("type") in {"function_call", "tool_call"} for item in payload.get("output", []) if isinstance(item, dict))
            or any("tool_calls" in choice.get("message", {}) for choice in payload.get("choices", []) if isinstance(choice, dict))
        ) else "text"
        if behavior not in {"hold", "delay"}:
            _log_request(body, path=self.path, scenario=scenario, model=ANTHROPIC_MODEL if anthropic else OPENAI_MODEL, provider=provider, usage=_response_usage(payload))
        _log_event(
            body,
            path=self.path,
            scenario=scenario,
            model=ANTHROPIC_MODEL if anthropic else OPENAI_MODEL,
            provider=provider,
            phase="completed",
            response_kind=response_kind,
        )
        if behavior != "error":
            self.send_response(200)
            if request.get("stream"):
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                if anthropic:
                    payload["usage"] = {"input_tokens": 12, "output_tokens": 0}
                    if tool:
                        events = [
                            ("message_start", {"type": "message_start", "message": payload}),
                            ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "tool_use", "id": "toolu_fake_1", "name": "Bash", "input": {}}}),
                            ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": '{"command":"echo YOLOMUX_FAKE_OK"}'}}),
                            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
                            ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 8}}),
                            ("message_stop", {"type": "message_stop"}),
                        ]
                    else:
                        events = [
                            ("message_start", {"type": "message_start", "message": {**payload, "content": [], "stop_reason": None}}),
                            ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
                            ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": response_text}}),
                            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
                            ("message_delta", {"type": "message_delta", "delta": {"stop_reason": payload["stop_reason"]}, "usage": {"output_tokens": 8}}),
                            ("message_stop", {"type": "message_stop"}),
                        ]
                    for event, value in events:
                        self.wfile.write(f"event: {event}\ndata: {json.dumps(value)}\n\n".encode())
                elif responses:
                    if tool:
                        events = [{"type": "response.created", "response": {"id": "resp_yolomux", "object": "response", "status": "in_progress", "model": OPENAI_MODEL}}, {"type": "response.output_item.added", "output_index": 0, "item": {"id": "call_fake_1", "type": "function_call", "call_id": "call_fake_1", "name": "exec_command", "arguments": ""}}, {"type": "response.function_call_arguments.delta", "item_id": "call_fake_1", "output_index": 0, "delta": '{"cmd":"echo YOLOMUX_FAKE_OK"}'}, {"type": "response.function_call_arguments.done", "item_id": "call_fake_1", "output_index": 0, "arguments": '{"cmd":"echo YOLOMUX_FAKE_OK"}'}, {"type": "response.output_item.done", "output_index": 0, "item": payload["output"][0]}, {"type": "response.completed", "response": {**payload, "usage": {"input_tokens": 12, "output_tokens": 8, "total_tokens": 20}}}]
                    else:
                        events = [{"type": "response.created", "response": {"id": "resp_yolomux", "object": "response", "status": "in_progress", "model": OPENAI_MODEL}}, {"type": "response.output_item.added", "output_index": 0, "item": {"id": "item_yolomux", "type": "message", "role": "assistant", "status": "in_progress", "content": []}}, {"type": "response.content_part.added", "item_id": "item_yolomux", "output_index": 0, "content_index": 0, "part": {"type": "output_text", "text": "", "annotations": []}}, {"type": "response.output_text.delta", "delta": response_text, "item_id": "item_yolomux", "output_index": 0, "content_index": 0}, {"type": "response.output_text.done", "text": response_text, "item_id": "item_yolomux", "output_index": 0, "content_index": 0}, {"type": "response.content_part.done", "item_id": "item_yolomux", "output_index": 0, "content_index": 0, "part": {"type": "output_text", "text": response_text, "annotations": []}}, {"type": "response.output_item.done", "output_index": 0, "item": {"id": "item_yolomux", "type": "message", "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": response_text, "annotations": []}]}}, {"type": "response.completed", "response": {**payload, "usage": {"input_tokens": 12, "output_tokens": 8, "total_tokens": 20}}}]
                    for event in events:
                        self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
                    self.wfile.write(b"data: [DONE]\n\n")
                else:
                    if tool:
                        chunk = {"id": "chatcmpl_yolomux", "object": "chat.completion.chunk", "created": 1, "model": OPENAI_MODEL, "choices": [{"index": 0, "delta": {"role": "assistant", "tool_calls": [{"index": 0, "id": "call_fake_1", "type": "function", "function": {"name": "Bash", "arguments": '{"command":"echo YOLOMUX_FAKE_OK"}'}}]}, "finish_reason": "tool_calls"}]}
                        terminal = {"id": "chatcmpl_yolomux", "object": "chat.completion.chunk", "created": 1, "model": OPENAI_MODEL, "choices": [], "usage": _opencode_usage()}
                    else:
                        chunk = {"id": "chatcmpl_yolomux", "object": "chat.completion.chunk", "created": 1, "model": OPENAI_MODEL, "choices": [{"index": 0, "delta": {"role": "assistant", "content": response_text}, "finish_reason": None}]}
                        terminal = {"id": "chatcmpl_yolomux", "object": "chat.completion.chunk", "created": 1, "model": OPENAI_MODEL, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}], "usage": _opencode_usage()}
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\ndata: {json.dumps(terminal)}\n\ndata: [DONE]\n\n".encode())
                self.wfile.flush()
                self.close_connection = True
                return
        encoded = json.dumps(payload).encode()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *_args):
        return


def main():
    port = int(os.environ.get("FAKE_LLM_PORT", sys.argv[1] if len(sys.argv) > 1 else "0"))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(server.server_address[1], flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
