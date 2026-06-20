#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Shared text-client helpers for YOLOmux agent prototypes."""
from __future__ import annotations

import json
import os
import readline
import select
import shlex
import sys
import termios
import time
import tomllib
import tty
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


ANSI_AUX_DARK = "\033[38;5;250m"
ANSI_AUX_LIGHT = "\033[38;5;238m"
ANSI_PROMPT_DARK = "\033[1;36m"
ANSI_PROMPT_LIGHT = "\033[38;5;25m"
ANSI_RESET = "\033[0m"
OSC11_QUERY_TIMEOUT_SECONDS = 0.12
READLINE_START_IGNORE = "\001"
READLINE_END_IGNORE = "\002"
TRUE_VALUES = {"1", "true", "yes", "y", "on", "enable", "enabled"}
FALSE_VALUES = {"0", "false", "no", "n", "off", "disable", "disabled"}
ANSI_16_RGB = {
    0: (0, 0, 0),
    1: (128, 0, 0),
    2: (0, 128, 0),
    3: (128, 128, 0),
    4: (0, 0, 128),
    5: (128, 0, 128),
    6: (0, 128, 128),
    7: (192, 192, 192),
    8: (128, 128, 128),
    9: (255, 0, 0),
    10: (0, 255, 0),
    11: (255, 255, 0),
    12: (0, 0, 255),
    13: (255, 0, 255),
    14: (0, 255, 255),
    15: (255, 255, 255),
}


def configure_readline(commands: Iterable[str]) -> None:
    readline.parse_and_bind("set editing-mode emacs")
    readline.parse_and_bind("Control-a: beginning-of-line")
    readline.parse_and_bind("Control-e: end-of-line")
    readline.parse_and_bind("Control-k: kill-line")
    readline.parse_and_bind("Control-u: unix-line-discard")
    readline.parse_and_bind("Control-w: unix-word-rubout")
    readline.parse_and_bind("Control-y: yank")
    readline.parse_and_bind("Control-p: previous-history")
    readline.parse_and_bind("Control-n: next-history")
    readline.parse_and_bind("Control-r: reverse-search-history")
    readline.parse_and_bind("Meta-b: backward-word")
    readline.parse_and_bind("Meta-f: forward-word")
    readline.parse_and_bind("Meta-d: kill-word")
    readline.parse_and_bind("tab: complete")
    readline.set_completer(SlashCommandCompleter(commands))


class SlashCommandCompleter:
    def __init__(self, commands: Iterable[str]):
        self.commands = sorted(set(commands))

    def __call__(self, text: str, state: int) -> str | None:
        buffer = readline.get_line_buffer()
        if not buffer.startswith("/"):
            return None
        token = text[1:] if text.startswith("/") else text
        matches = [f"/{command}" for command in self.commands if command.startswith(token)]
        if state < len(matches):
            return matches[state]
        return None


def parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    raise ValueError(f"expected on/off, got {value!r}")


def parse_config_value(raw_value: str) -> Any:
    try:
        return tomllib.loads(f"value = {raw_value}")["value"]
    except tomllib.TOMLDecodeError:
        return raw_value


def parse_config_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return parse_bool(value)
    return bool(value)


def config_value_text(value: Any) -> str:
    return json.dumps(value, sort_keys=True)


def display_value(value: Any) -> str:
    if value is True:
        return "on"
    if value is False:
        return "off"
    if value in {"", None}:
        return "<default>"
    return str(value)


def color_text(text: str, enabled: bool, color: str = ANSI_AUX_DARK) -> str:
    return f"{color}{text}{ANSI_RESET}" if enabled else text


def color_prompt(text: str, enabled: bool, color: str = ANSI_PROMPT_DARK) -> str:
    if not enabled:
        return text
    return f"{READLINE_START_IGNORE}{color}{READLINE_END_IGNORE}{text}{READLINE_START_IGNORE}{ANSI_RESET}{READLINE_END_IGNORE}"


def shorten_cwd(cwd: str) -> str:
    resolved = str(Path(cwd).expanduser().resolve())
    home = str(Path.home())
    if resolved == home:
        return "~"
    if resolved.startswith(home + os.sep):
        return "~/" + resolved[len(home) + 1 :]
    return resolved


def relative_luminance(rgb: tuple[int, int, int]) -> float:
    red, green, blue = rgb
    return (0.2126 * red + 0.7152 * green + 0.0722 * blue) / 255


@dataclass(frozen=True)
class TerminalBackground:
    mode: str
    source: str
    luminance: float | None = None


@dataclass(frozen=True)
class TerminalPalette:
    background: TerminalBackground
    aux_color: str
    prompt_color: str


def background_for_rgb(rgb: tuple[int, int, int], source: str) -> TerminalBackground:
    luminance = relative_luminance(rgb)
    return TerminalBackground("light" if luminance >= 0.5 else "dark", source, luminance)


def background_from_colorfgbg(value: str) -> TerminalBackground | None:
    parts = [part for part in value.replace(":", ";").split(";") if part]
    if not parts:
        return None
    try:
        background = int(parts[-1])
    except ValueError:
        return None
    rgb = ANSI_16_RGB.get(background % 16)
    if rgb is None:
        return None
    return background_for_rgb(rgb, "COLORFGBG")


def parse_osc11_rgb(response: str) -> tuple[int, int, int] | None:
    marker = "]11;"
    marker_index = response.find(marker)
    if marker_index < 0:
        return None
    payload = response[marker_index + len(marker) :]
    for terminator in ("\x07", "\x1b\\"):
        terminator_index = payload.find(terminator)
        if terminator_index >= 0:
            payload = payload[:terminator_index]
            break
    if ":" not in payload:
        return None
    color_type, raw_channels = payload.split(":", 1)
    if color_type not in {"rgb", "rgba"}:
        return None
    channels = raw_channels.split("/")
    if len(channels) < 3:
        return None
    rgb: list[int] = []
    for channel in channels[:3]:
        digits = "".join(char for char in channel if char in "0123456789abcdefABCDEF")
        if not digits:
            return None
        channel_value = int(digits, 16)
        channel_max = (16 ** len(digits)) - 1
        rgb.append(round(channel_value * 255 / channel_max))
    return rgb[0], rgb[1], rgb[2]


def read_terminal_response(fd: int, timeout: float) -> str:
    deadline = time.monotonic() + timeout
    chunks: list[str] = []
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return "".join(chunks)
        readable, _, _ = select.select([fd], [], [], remaining)
        if not readable:
            return "".join(chunks)
        data = os.read(fd, 64)
        if not data:
            return "".join(chunks)
        chunk = data.decode("ascii", errors="ignore")
        chunks.append(chunk)
        response = "".join(chunks)
        if "\x07" in response or "\x1b\\" in response:
            return response


def query_terminal_background(timeout: float = OSC11_QUERY_TIMEOUT_SECONDS) -> TerminalBackground | None:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return None
    term = os.environ.get("TERM", "")
    if not term or term == "dumb":
        return None
    try:
        fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
    except OSError:
        return None
    try:
        old_attrs = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd)
            os.write(fd, b"\033]11;?\a")
            response = read_terminal_response(fd, timeout)
        finally:
            termios.tcsetattr(fd, termios.TCSANOW, old_attrs)
    except (OSError, termios.error):
        return None
    finally:
        os.close(fd)
    rgb = parse_osc11_rgb(response)
    if rgb is None:
        return None
    return background_for_rgb(rgb, "osc11")


def background_from_override() -> TerminalBackground | None:
    for name in ("TEXT_CLIENT_BACKGROUND", "YOLOMUX_TEXT_CLIENT_BACKGROUND"):
        override = os.environ.get(name)
        if not override:
            continue
        normalized = override.strip().lower()
        if normalized in {"dark", "black"}:
            return TerminalBackground("dark", name)
        if normalized in {"light", "white"}:
            return TerminalBackground("light", name)
    return None


def terminal_background_mode() -> TerminalBackground:
    override = background_from_override()
    if override is not None:
        return override
    queried = query_terminal_background()
    if queried is not None:
        return queried
    colorfgbg = os.environ.get("COLORFGBG", "")
    if colorfgbg:
        parsed = background_from_colorfgbg(colorfgbg)
        if parsed is not None:
            return parsed
    return TerminalBackground("unknown", "fallback")


def terminal_palette() -> TerminalPalette:
    background = terminal_background_mode()
    if background.mode == "light":
        return TerminalPalette(background, ANSI_AUX_LIGHT, ANSI_PROMPT_LIGHT)
    return TerminalPalette(background, ANSI_AUX_DARK, ANSI_PROMPT_DARK)


def command_text(parts: Iterable[Any]) -> str:
    return shlex.join(str(part) for part in parts)


TOKEN_KEY_ALIASES = {
    "input_tokens": ("input_tokens", "inputTokens", "prompt_tokens", "promptTokens", "inputTokenCount", "promptTokenCount"),
    "output_tokens": ("output_tokens", "outputTokens", "completion_tokens", "completionTokens", "outputTokenCount", "completionTokenCount"),
    "reasoning_tokens": ("reasoning_tokens", "reasoningTokens", "reasoningTokenCount"),
    "cached_input_tokens": ("cached_input_tokens", "cachedInputTokens", "cache_read_input_tokens", "cacheReadInputTokens", "cached_tokens", "cachedTokens"),
    "total_tokens": ("total_tokens", "totalTokens", "totalTokenCount"),
}


def numeric_metric_value(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return float(stripped)
        except ValueError:
            return None
    return None


def rough_token_count_for_chars(char_count: int) -> int:
    if char_count <= 0:
        return 0
    return max(1, (char_count + 3) // 4)


def rough_token_count(text: str) -> int:
    return rough_token_count_for_chars(len(text.strip()))


def format_seconds(value: float | None) -> str:
    if value is None:
        return "n/a"
    if value < 1:
        return f"{value * 1000:.0f}ms"
    return f"{value:.3f}s"


def format_number(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return f"{value:.1f}"


def format_rate(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2f}/s"


def elapsed_seconds(start: float, end: float) -> float | None:
    if not start or not end or end < start:
        return None
    return end - start


@dataclass
class TurnMetrics:
    input_text: str
    submitted_at: float
    status: str = "running"
    thread_ready_at: float = 0.0
    turn_start_request_at: float = 0.0
    turn_start_response_at: float = 0.0
    turn_started_at: float = 0.0
    first_server_message_at: float = 0.0
    first_answer_at: float = 0.0
    first_reasoning_at: float = 0.0
    first_tool_at: float = 0.0
    completed_at: float = 0.0
    server_messages: int = 0
    answer_chars: int = 0
    answer_chunks: int = 0
    reasoning_summary_chars: int = 0
    reasoning_summary_chunks: int = 0
    raw_reasoning_chars: int = 0
    raw_reasoning_chunks: int = 0
    tool_output_chars: int = 0
    tool_output_chunks: int = 0
    approval_requests: int = 0
    tool_duration_ms: float = 0.0
    tool_duration_count: int = 0
    method_counts: dict[str, int] = field(default_factory=dict)
    item_started_counts: dict[str, int] = field(default_factory=dict)
    item_completed_counts: dict[str, int] = field(default_factory=dict)
    usage: dict[str, float] = field(default_factory=dict)

    def mark_server_message(self, method: str, now: float) -> None:
        self.server_messages += 1
        if not self.first_server_message_at:
            self.first_server_message_at = now
        if method:
            self.method_counts[method] = self.method_counts.get(method, 0) + 1

    def record_answer_text(self, text: str, now: float) -> None:
        if not text:
            return
        if not self.first_answer_at:
            self.first_answer_at = now
        self.answer_chars += len(text)
        self.answer_chunks += 1

    def record_reasoning_summary(self, text: str, now: float) -> None:
        if not text:
            return
        if not self.first_reasoning_at:
            self.first_reasoning_at = now
        self.reasoning_summary_chars += len(text)
        self.reasoning_summary_chunks += 1

    def record_raw_reasoning(self, text: str, now: float) -> None:
        if not text:
            return
        if not self.first_reasoning_at:
            self.first_reasoning_at = now
        self.raw_reasoning_chars += len(text)
        self.raw_reasoning_chunks += 1

    def record_tool_output(self, text: str, now: float) -> None:
        if not text:
            return
        if not self.first_tool_at:
            self.first_tool_at = now
        self.tool_output_chars += len(text)
        self.tool_output_chunks += 1

    def record_tool_item(self, event: str, item: dict[str, Any], now: float) -> None:
        item_type = str(item.get("type") or "").strip() or "unknown"
        if item_type in {"commandExecution", "mcpToolCall", "dynamicToolCall", "webSearch", "fileChange", "tool_use"} and not self.first_tool_at:
            self.first_tool_at = now
        if event == "start":
            self.item_started_counts[item_type] = self.item_started_counts.get(item_type, 0) + 1
            return
        self.item_completed_counts[item_type] = self.item_completed_counts.get(item_type, 0) + 1
        duration_ms = numeric_metric_value(item.get("durationMs"))
        if duration_ms is not None:
            self.tool_duration_ms += duration_ms
            self.tool_duration_count += 1

    def merge_usage(self, key: str, value: float) -> None:
        current = self.usage.get(key)
        if current is None or value > current:
            self.usage[key] = value


def collect_token_usage(payload: Any, metrics: TurnMetrics, depth: int = 0) -> None:
    if depth > 8:
        return
    if isinstance(payload, dict):
        for canonical_key, aliases in TOKEN_KEY_ALIASES.items():
            for alias in aliases:
                if alias in payload:
                    number = numeric_metric_value(payload[alias])
                    if number is not None:
                        metrics.merge_usage(canonical_key, number)
        for value in payload.values():
            if isinstance(value, (dict, list)):
                collect_token_usage(value, metrics, depth + 1)
        return
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, (dict, list)):
                collect_token_usage(item, metrics, depth + 1)


class TextClientBase:
    def __init__(self, cwd: str, prefixed_labels: Iterable[str]):
        self.cwd = cwd
        palette = terminal_palette()
        self.terminal_background = palette.background.mode
        self.terminal_background_source = palette.background.source
        self.terminal_background_luminance = palette.background.luminance
        self.aux_color = palette.aux_color
        self.prompt_color = palette.prompt_color
        self.use_aux_color = sys.stdout.isatty()
        self.use_aux_stderr_color = sys.stderr.isatty()
        self.use_prompt_color = sys.stdout.isatty()
        self.answer_output_at_line_start = True
        self.prefixed_output_at_line_start = {label: True for label in prefixed_labels}
        self.current_metrics: TurnMetrics | None = None
        self.last_metrics: TurnMetrics | None = None

    def display_cwd(self) -> str:
        return shorten_cwd(self.cwd)

    def prompt_text_for(self, model: str, effort: str) -> str:
        return color_prompt(f"{model}[{effort}] {self.display_cwd()}› ", self.use_prompt_color, self.prompt_color)

    def terminal_background_text(self) -> str:
        text = f"{self.terminal_background} ({self.terminal_background_source})"
        if self.terminal_background_luminance is not None:
            text += f", luminance {self.terminal_background_luminance:.2f}"
        return text

    def print_compat_note(self, command: str, native_client: str, reason: str) -> None:
        self.print_aux_stderr(f"/{command}: compatibility command; native {native_client} may not provide this slash command. {reason}")

    def print_aux_stderr(self, text: str) -> None:
        print(color_text(text, self.use_aux_stderr_color, self.aux_color), file=sys.stderr)

    def finish_answer_output(self) -> None:
        if not self.answer_output_at_line_start:
            print("", flush=True)
            self.answer_output_at_line_start = True

    def write_prefixed_stdout(self, label: str, text: str) -> None:
        if not text:
            return
        self.finish_answer_output()
        for other_label, at_line_start in self.prefixed_output_at_line_start.items():
            if other_label != label and not at_line_start:
                print("", flush=True)
                self.prefixed_output_at_line_start[other_label] = True
        prefix = f"{label}| "
        at_line_start = self.prefixed_output_at_line_start[label]
        for chunk in text.splitlines(keepends=True):
            if at_line_start:
                print(color_text(prefix, self.use_aux_color, self.aux_color), end="", flush=True)
            print(color_text(chunk, self.use_aux_color, self.aux_color), end="", flush=True)
            at_line_start = chunk.endswith(("\n", "\r"))
        self.prefixed_output_at_line_start[label] = at_line_start

    def write_prefixed_line(self, label: str, text: str) -> None:
        self.write_prefixed_stdout(label, f"{text}\n")

    def finish_prefixed_output(self) -> None:
        for label, at_line_start in self.prefixed_output_at_line_start.items():
            if not at_line_start:
                print("", flush=True)
                self.prefixed_output_at_line_start[label] = True

    def start_metrics(self, text: str) -> TurnMetrics:
        self.current_metrics = TurnMetrics(text, time.monotonic())
        return self.current_metrics

    def finish_metrics(self, status: str, show_metrics: bool) -> None:
        metrics = self.current_metrics
        if metrics is None:
            return
        metrics.status = status
        metrics.completed_at = time.monotonic()
        self.last_metrics = metrics
        self.current_metrics = None
        if show_metrics:
            self.print_metrics(metrics)

    def print_metrics(self, metrics: TurnMetrics | None = None) -> None:
        selected_metrics = metrics or self.last_metrics
        if selected_metrics is None:
            print("No turn metrics yet.")
            return
        for line in self.metrics_lines(selected_metrics):
            self.write_prefixed_line("metrics", line)

    def metric_token_value(self, metrics: TurnMetrics, key: str, estimate: int, estimate_source: str) -> tuple[float, str]:
        value = metrics.usage.get(key)
        if value is not None:
            return value, "server"
        return float(estimate), estimate_source

    def metrics_lines(self, metrics: TurnMetrics) -> list[str]:
        start_at = metrics.turn_start_request_at or metrics.submitted_at
        first_event = elapsed_seconds(metrics.submitted_at, metrics.first_server_message_at)
        ttft = elapsed_seconds(start_at, metrics.first_answer_at)
        submit_to_first = elapsed_seconds(metrics.submitted_at, metrics.first_answer_at)
        total = elapsed_seconds(metrics.submitted_at, metrics.completed_at)
        thread_time = elapsed_seconds(metrics.submitted_at, metrics.thread_ready_at)
        turn_start_time = elapsed_seconds(metrics.turn_start_request_at, metrics.turn_start_response_at)
        first_reasoning = elapsed_seconds(start_at, metrics.first_reasoning_at)
        first_tool = elapsed_seconds(start_at, metrics.first_tool_at)
        input_tokens, input_source = self.metric_token_value(metrics, "input_tokens", rough_token_count(metrics.input_text), "est_user_prompt")
        output_tokens, output_source = self.metric_token_value(metrics, "output_tokens", rough_token_count_for_chars(metrics.answer_chars), "est_answer")
        total_tokens = metrics.usage.get("total_tokens")
        total_source = "server"
        if total_tokens is None:
            total_tokens = input_tokens + output_tokens
            total_source = "est_sum"
        decode_seconds = elapsed_seconds(metrics.first_answer_at, metrics.completed_at)
        tokens_sec = output_tokens / decode_seconds if decode_seconds and output_tokens else None
        chars_sec = metrics.answer_chars / decode_seconds if decode_seconds and metrics.answer_chars else None
        lines = [
            "status="
            + metrics.status
            + "; latency: TTFT="
            + format_seconds(ttft)
            + ", submit_to_first="
            + format_seconds(submit_to_first)
            + ", first_event="
            + format_seconds(first_event)
            + ", first_reasoning="
            + format_seconds(first_reasoning)
            + ", first_tool="
            + format_seconds(first_tool)
            + ", total="
            + format_seconds(total),
            "setup: thread="
            + format_seconds(thread_time)
            + ", turn_start="
            + format_seconds(turn_start_time)
            + ", server_messages="
            + str(metrics.server_messages),
            "tokens: ISL="
            + format_number(input_tokens)
            + " "
            + input_source
            + ", OSL="
            + format_number(output_tokens)
            + " "
            + output_source
            + ", total="
            + format_number(total_tokens)
            + " "
            + total_source
            + ", output_tokens_sec="
            + format_rate(tokens_sec)
            + ", answer_chars_sec="
            + format_rate(chars_sec),
            "stream: answer_chars="
            + str(metrics.answer_chars)
            + ", answer_chunks="
            + str(metrics.answer_chunks)
            + ", reasoning_summary_chars="
            + str(metrics.reasoning_summary_chars)
            + ", raw_reasoning_chars="
            + str(metrics.raw_reasoning_chars)
            + ", tool_output_chars="
            + str(metrics.tool_output_chars),
        ]
        token_extras = []
        for key in ["cached_input_tokens", "reasoning_tokens"]:
            value = metrics.usage.get(key)
            if value is not None:
                token_extras.append(f"{key}={format_number(value)}")
        if token_extras:
            lines.append("usage extras: " + ", ".join(token_extras))
        tool_parts = []
        for item_type, label in [
            ("commandExecution", "command"),
            ("mcpToolCall", "mcp"),
            ("dynamicToolCall", "dynamic"),
            ("webSearch", "web"),
            ("fileChange", "file"),
            ("tool_use", "tool_use"),
        ]:
            started = metrics.item_started_counts.get(item_type, 0)
            completed = metrics.item_completed_counts.get(item_type, 0)
            if started or completed:
                tool_parts.append(f"{label}={started}/{completed}")
        if tool_parts or metrics.tool_duration_count or metrics.approval_requests:
            duration_text = format_seconds(metrics.tool_duration_ms / 1000) if metrics.tool_duration_count else "n/a"
            lines.append("tools: " + (", ".join(tool_parts) if tool_parts else "none") + ", approvals=" + str(metrics.approval_requests) + ", tool_duration=" + duration_text)
        top_methods = sorted(metrics.method_counts.items(), key=lambda item: item[1], reverse=True)[:6]
        if top_methods:
            lines.append("events: " + ", ".join(f"{method}={count}" for method, count in top_methods))
        return lines
