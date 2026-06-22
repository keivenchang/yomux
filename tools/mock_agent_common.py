#!/usr/bin/env python3
"""Shared mock agent terminal for YOLOmux UI testing."""

import atexit
import os
from pathlib import Path
import random
import re
import readline
import select
import shutil
import subprocess
import sys
import termios
import threading
import textwrap
import time
import tty
import uuid

import yaml


AGENT_NAME = "claude"
AGENT_DISPLAY_NAME = "Claude"
AGENT_PRODUCT_NAME = "Claude Code"
HISTORY_FILE = os.path.expanduser("~/.cache/yolomux/mock_claude_history")
HISTORY_LIMIT = 1000

VERSION = ".9.9.999"
MODEL = "Opus 4.7 (1M context)"
EFFORT = "low"
MODEL_LINE = f"{MODEL} with {EFFORT} effort · API Usage Billing"
DEFAULT_WIDTH = 126
# Real Claude AND real Codex both render the permission-choice selector and the input prompt with
# `›` (U+203A SINGLE RIGHT-POINTING ANGLE QUOTATION MARK), NOT `❯` (U+276F). The mocks exist to
# exercise the real-agent-tuned detector, so they must match the real glyph: real Claude uses ❯
# (U+276F), real Codex uses › (U+203A). Each mock overrides these per-agent; detection is
# glyph-agnostic regardless (the choice/selector regexes accept ❯, ›, and >).
PROMPT_GLYPH = "❯"
SELECTOR_GLYPH = "❯"
PERMISSION_STYLE = "claude"
STARTUP_STYLE = "default"
CODEX_BYPASS_HOOK_TRUST = False
CODEX_DANGER_FULL_ACCESS = False
CLAUDE_MODE_STATUS_LINES = [
    "",
    "  ⏵⏵ accept edits on (shift+tab to cycle)",
    "  ⏸ plan mode on (shift+tab to cycle)",
    "  ⏵⏵ auto mode on (shift+tab to cycle)",
]
PROMPT_CORPUS_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "prompt_corpus"
MOCK_FIXTURE_CASES: list[dict[str, object]] | None = None

VERBS = [
    "Hashing", "Cooking", "Pondering", "Distilling", "Considering",
    "Thinking", "Mulling", "Plotting", "Cogitating", "Riffing",
    "Brewing", "Forging", "Crystallizing", "Untangling", "Inferring",
    "Synthesizing", "Speculating", "Refining", "Sleuthing", "Marinating",
    "Percolating", "Steeping", "Brainstorming", "Calibrating", "Concocting",
    "Conjuring", "Deciphering", "Decoding", "Excavating", "Exploring",
    "Filtering", "Gleaning", "Honing", "Investigating", "Massaging",
    "Meditating", "Munging", "Navigating", "Noodling", "Orchestrating",
    "Probing", "Reasoning", "Reckoning", "Reflecting", "Ruminating",
    "Scheming", "Scrutinizing", "Sifting", "Simmering", "Studying",
    "Surveying", "Tinkering", "Tracing", "Tuning", "Weaving",
    "Winnowing", "Wondering", "Wrangling",
]

PAST_VERBS = [
    "Hashed", "Cooked", "Pondered", "Distilled", "Considered",
    "Thought", "Mulled", "Plotted", "Cogitated", "Riffed",
    "Brewed", "Forged", "Crystallized", "Untangled", "Inferred",
    "Synthesized", "Speculated", "Refined", "Sleuthed", "Marinated",
    "Percolated", "Steeped", "Brainstormed", "Calibrated", "Concocted",
    "Conjured", "Deciphered", "Decoded", "Excavated", "Explored",
    "Filtered", "Gleaned", "Honed", "Investigated", "Massaged",
    "Meditated", "Munged", "Navigated", "Noodled", "Orchestrated",
    "Probed", "Reasoned", "Reckoned", "Reflected", "Ruminated",
    "Schemed", "Scrutinized", "Sifted", "Simmered", "Studied",
    "Surveyed", "Tinkered", "Traced", "Tuned", "Weaved",
    "Winnowed", "Wondered", "Wrangled", "Sautéed", "Baked",
    "Stewed", "Braised", "Roasted", "Grilled", "Whisked",
]

FRAMES = ["·", "✢", "✶", "✻", "✽", "*"]
CLAUDE_WORKING_VERBS = VERBS + [
    "Imagining", "Transmogrifying", "Combobulating", "Recombobulating",
    "Perambulating", "Doodling", "Frobnitzing", "Hypernoodling",
]
CLAUDE_TIPS = [
    "Connect Claude to your IDE · /ide",
    "Press Esc to interrupt",
    "Use ctrl+t to hide tasks",
]

SHELL_COMMANDS = {
    "ls", "pwd", "cat", "head", "tail", "grep", "find", "ps", "df", "du",
    "free", "whoami", "hostname", "uname", "date", "uptime", "id", "groups",
    "git", "docker", "tmux", "kubectl", "ssh", "scp", "rsync", "curl", "wget",
    "echo", "printf", "which", "type", "history", "env",
    "python", "python3", "pip", "node", "npm", "cargo", "rustc", "go",
    "make", "cmake", "gcc", "clang",
    "less", "more", "diff",
    "ping", "netstat", "ss", "ip", "ifconfig",
    "base64", "md5sum", "sha256sum",
    "tar", "gzip", "gunzip", "zip", "unzip",
    "kill", "pgrep", "jobs", "stat", "file", "touch", "chmod",
    "awk", "sed", "tr", "cut", "sort", "uniq", "wc",
    "xargs", "tee", "watch", "nohup",
}

COMMAND_DESCRIPTIONS = {
    "date": "Show date/time",
    "pwd": "Show working directory",
    "whoami": "Show current user",
    "hostname": "Show hostname",
    "uname": "Show system info",
    "ls": "List directory contents",
    "ps": "List processes",
    "df": "Show disk usage",
    "du": "Show disk usage",
    "free": "Show memory usage",
    "uptime": "Show uptime",
    "git": "Run git command",
    "docker": "Run docker command",
    "tmux": "Run tmux command",
    "cat": "Read file contents",
    "head": "Show file head",
    "tail": "Show file tail",
    "grep": "Search for pattern",
    "find": "Find files",
    "echo": "Print text",
    "env": "Show environment",
    "ping": "Send ping",
    "curl": "HTTP request",
    "wget": "Download URL",
}


def configure(
    *,
    agent_name: str,
    agent_display_name: str,
    agent_product_name: str,
    history_file: str,
    version: str,
    model: str,
    effort: str,
    model_line: str,
    prompt_glyph: str,
    selector_glyph: str,
    permission_style: str,
    startup_style: str = "default",
    codex_bypass_hook_trust: bool = False,
    codex_danger_full_access: bool = False,
) -> None:
    global AGENT_NAME, AGENT_DISPLAY_NAME, AGENT_PRODUCT_NAME, HISTORY_FILE
    global VERSION, MODEL, EFFORT, MODEL_LINE, PROMPT_GLYPH, SELECTOR_GLYPH, PERMISSION_STYLE
    global STARTUP_STYLE, CODEX_BYPASS_HOOK_TRUST, CODEX_DANGER_FULL_ACCESS

    AGENT_NAME = agent_name
    AGENT_DISPLAY_NAME = agent_display_name
    AGENT_PRODUCT_NAME = agent_product_name
    HISTORY_FILE = os.path.expanduser(history_file)
    VERSION = version
    MODEL = model
    EFFORT = effort
    MODEL_LINE = model_line
    PROMPT_GLYPH = prompt_glyph
    SELECTOR_GLYPH = selector_glyph
    PERMISSION_STYLE = permission_style
    STARTUP_STYLE = startup_style
    CODEX_BYPASS_HOOK_TRUST = codex_bypass_hook_trust
    CODEX_DANGER_FULL_ACCESS = codex_danger_full_access


def looks_like_shell_command(value: str) -> bool:
    tokens = value.split()
    if not tokens:
        return False
    return tokens[0] in SHELL_COMMANDS


def describe_shell_command(value: str) -> str:
    tokens = value.split()
    if not tokens:
        return "Run shell command"
    return COMMAND_DESCRIPTIONS.get(tokens[0], "Run shell command")


def terminal_width() -> int:
    try:
        columns = os.get_terminal_size(sys.stdout.fileno()).columns
    except OSError:
        columns = shutil.get_terminal_size((DEFAULT_WIDTH, 24)).columns
    # Respect the real pane width (cap at 150). A hard floor wider than the actual
    # terminal makes the welcome box overflow and wrap ("chopped up"); when it can't
    # fit two columns, print_welcome_box falls back to the minimal header instead.
    return max(40, min(columns, 150))


def terminal_height() -> int:
    try:
        return max(1, os.get_terminal_size(sys.stdout.fileno()).lines)
    except OSError:
        return shutil.get_terminal_size((DEFAULT_WIDTH, 40)).lines


def ctrl_c_requests_exit(state: dict[str, str], now: float | None = None) -> bool:
    now = time.monotonic() if now is None else now
    previous = float(state.get("last_ctrl_c_at", "0") or 0)
    state["last_ctrl_c_at"] = str(now)
    return previous > 0


def clear_ctrl_c_exit_window(state: dict[str, str]) -> None:
    state.pop("last_ctrl_c_at", None)


def clipped(text: str, width: int) -> str:
    value = str(text)
    if len(value) <= width:
        return value + (" " * (width - len(value)))
    if width <= 1:
        return value[:width]
    return value[: width - 1] + "…"


def full_rule(width: int) -> str:
    return "─" * width


def prompt_rule(width: int) -> None:
    print(full_rule(width))


def print_prompt_box(text: str, width: int) -> None:
    inner = width - 2
    print("╭" + ("─" * inner) + "╮")
    content = " " + text
    print("│" + clipped(content, inner) + "│")
    print("╰" + ("─" * inner) + "╯")


def print_user_header(text: str) -> None:
    """Dark-bg header showing the user's submitted input at the top of a turn."""
    width = terminal_width()
    content = f"> {text}"
    if len(content) > width:
        content = content[: width - 1] + "…"
    pad = max(0, width - len(content))
    sys.stdout.write(f"\x1b[100m{content}{' ' * pad}\x1b[0m\n")
    sys.stdout.flush()


def display_cwd() -> str:
    cwd = os.getcwd()
    home = os.path.expanduser("~")
    if cwd == home:
        return "~"
    if cwd.startswith(home + "/"):
        return "~" + cwd[len(home):]
    return cwd


def print_startup() -> None:
    if STARTUP_STYLE == "codex":
        print_codex_startup()
        return
    width = terminal_width()
    print_minimal_header()
    if not sys.stdout.isatty():
        print()
        print_prompt_box(f'{PROMPT_GLYPH} Try "fix typecheck errors"', width)
        print()


def print_minimal_header() -> None:
    print(f" {CLAUDE_ORANGE}▐▛███▜▌{ANSI_RESET}   {AGENT_PRODUCT_NAME}{ANSI_DIM} v{VERSION}{ANSI_RESET}")
    print(f"{CLAUDE_ORANGE}▝▜█████▛▘{ANSI_RESET}  {ANSI_DIM}{MODEL_LINE}{ANSI_RESET}")
    print(f"  {CLAUDE_ORANGE}▘▘ ▝▝{ANSI_RESET}    {ANSI_DIM}{display_cwd()}{ANSI_RESET}")


def centered_in(text: str, width: int) -> str:
    if len(text) >= width:
        return text[:width]
    pad = width - len(text)
    left = pad // 2
    return (" " * left) + text + (" " * (pad - left))


# Claude Code welcome-box palette: truecolor "Claude orange" plus standard SGR.
CLAUDE_ORANGE = "\x1b[38;2;215;119;87m"
ANSI_BOLD = "\x1b[1m"
ANSI_DIM = "\x1b[2m"
ANSI_ITALIC = "\x1b[3m"
ANSI_RESET = "\x1b[0m"
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
# Enterprise identity lines shown under the robot in the real welcome box.
WELCOME_ORG_LINE = "· Acme Corp - Power Users"
WELCOME_PLAN_LABEL = "Claude Enterprise"


def visible_len(text: str) -> int:
    """Display width of a string, ignoring ANSI SGR color escapes."""
    return len(ANSI_RE.sub("", text))


def pad_cell(text: str, width: int) -> str:
    """Left-justify to a visible width so embedded color codes don't shift the
    box borders. Colored cells (always short here) are only padded; plain text
    that overflows is clipped with an ellipsis."""
    vis = visible_len(text)
    if vis <= width:
        return text + (" " * (width - vis))
    return clipped(ANSI_RE.sub("", text), width)


def center_cell(text: str, width: int) -> str:
    """Center on visible width, ignoring ANSI color escapes."""
    vis = visible_len(text)
    if vis >= width:
        return pad_cell(text, width)
    pad = width - vis
    left = pad // 2
    return (" " * left) + text + (" " * (pad - left))


def print_welcome_box() -> None:
    width = terminal_width()
    inner = width - 2
    left_w = 60
    right_w = inner - left_w - 1
    if right_w < 24:
        print_minimal_header()
        return

    user_name = os.environ.get("USER") or "there"
    user_name = user_name[:1].upper() + user_name[1:]

    welcome = f"{ANSI_BOLD}Welcome back {user_name}!{ANSI_RESET}"
    # Real Claude centers the robot in the left column with a blank row above it; the
    # version/title goes in the TOP BORDER (below), not in a content row.
    robot = [
        f"{CLAUDE_ORANGE}▐▛███▜▌{ANSI_RESET}",
        f"{CLAUDE_ORANGE}▝▜█████▛▘{ANSI_RESET}",
        f"{CLAUDE_ORANGE}▘▘ ▝▝{ANSI_RESET}",
    ]
    left_lines = [
        "",
        center_cell(welcome, left_w),
        "",
        center_cell(robot[0], left_w),
        center_cell(robot[1], left_w),
        center_cell(robot[2], left_w),
        f" {ANSI_DIM}{MODEL_LINE}{ANSI_RESET}",
        f" {ANSI_DIM}{WELCOME_ORG_LINE}{ANSI_RESET}",
        center_cell(f"{ANSI_DIM}{display_cwd()}{ANSI_RESET}", left_w),
    ]

    tip = "Ask Claude to create a new app or clone a repository"
    whats_new = [
        "Internal infrastructure improvements (no user-facing changes)",
        "Auto mode is now available on Bedrock, Vertex AI, and more",
        "Plugins in `.claude/skills` directories are now supported",
    ]
    right_lines = [
        f"{CLAUDE_ORANGE}{ANSI_BOLD}Tips for getting started{ANSI_RESET}",
        tip,
        f"{ANSI_DIM}{'─' * max(0, right_w - 1)}{ANSI_RESET}",
        f"{CLAUDE_ORANGE}{ANSI_BOLD}What's new{ANSI_RESET}",
        whats_new[0],
        whats_new[1],
        whats_new[2],
        f"{ANSI_DIM}{ANSI_ITALIC}/release-notes for more{ANSI_RESET}",
        "",
    ]

    # Title lives IN the top border, like real Claude: ╭─── Claude Code vX.Y.Z Mock ───╮
    title = f" {AGENT_PRODUCT_NAME} v{VERSION} Mock "
    trailing = inner - 3 - len(title)
    if trailing >= 0:
        top = "╭───" + title + ("─" * trailing) + "╮"
    else:  # extremely narrow box: plain border, title falls back to the first row
        top = "╭" + ("─" * inner) + "╮"
        left_lines[0] = pad_cell(f"{AGENT_PRODUCT_NAME} v{VERSION} Mock", left_w)
    print(top)
    for left, right in zip(left_lines, right_lines):
        print("│" + pad_cell(left, left_w) + "│" + pad_cell(" " + right, right_w) + "│")
    print("╰" + ("─" * inner) + "╯")


def print_codex_startup() -> None:
    inner = 56

    def box_line(text: str = "") -> str:
        return "│" + clipped(text, inner) + "│"

    print("╭" + ("─" * inner) + "╮")
    print(box_line(f" >_ {AGENT_PRODUCT_NAME} (v{VERSION})"))
    print(box_line())
    print(box_line(f" model:     {MODEL} {EFFORT}   /model to change"))
    print(box_line(f" directory: {display_cwd()}"))
    # Real Codex only shows the danger banner when launched in full-access mode;
    # otherwise there is no permissions line. Mirror that — only show it when
    # mock_codex.py was passed --dangerously-bypass-approvals-and-sandbox.
    if CODEX_DANGER_FULL_ACCESS:
        print(box_line(" permissions: danger-full-access"))
    print("╰" + ("─" * inner) + "╯")
    print()
    if CODEX_BYPASS_HOOK_TRUST:
        print("  ⚠ `--dangerously-bypass-hook-trust` enabled")
        print()
    print("  Tip: When the composer is empty, press Esc to step back and edit your last message; Enter confirms.")
    print()
    if not sys.stdout.isatty():
        print()
        print(f"{PROMPT_GLYPH} {live_composer_suggestion()}")
        print()
        print(f"{MODEL} {EFFORT} · {display_cwd()}")
        print()


def print_thinking(seconds: float = 0.5, tokens: int = 39) -> None:
    """Animated thinking spinner. Brief by default (~500ms)."""
    print()
    if PERMISSION_STYLE == "codex":
        print_codex_working(seconds)
        return
    if not sys.stdout.isatty():
        print(f"✻ {random.choice(VERBS)}… ({seconds}s · ↓ {tokens} tokens · thinking with {EFFORT} effort)")
        return
    tick = 0.05
    total_ticks = max(1, int(seconds / tick))
    verb = random.choice(VERBS)
    for i in range(total_ticks + 1):
        elapsed = i * tick
        sec_shown = max(1, int(elapsed * 10) / 10 if elapsed < 1 else int(elapsed))
        tok_shown = min(tokens, max(1, int(elapsed * (tokens / max(0.1, seconds)))))
        frame = FRAMES[i % len(FRAMES)]
        line = f"{frame} {verb}… ({sec_shown}s · ↓ {tok_shown} tokens · thinking with {EFFORT} effort · esc to interrupt)"
        sys.stdout.write("\r\x1b[2K" + line)
        sys.stdout.flush()
        if i < total_ticks:
            time.sleep(tick)
    sys.stdout.write("\r\x1b[2K")
    sys.stdout.flush()


def format_working_elapsed(seconds: float) -> str:
    total = max(1, int(seconds))
    minutes, remaining = divmod(total, 60)
    if minutes:
        return f"{minutes}m {remaining}s"
    return f"{remaining}s"


def codex_working_line(seconds: float) -> str:
    return "• Working (... • esc to interrupt)"


def print_codex_working(seconds: float) -> None:
    if not sys.stdout.isatty():
        print(codex_working_line(seconds))
        return

    tick = 0.12
    total_ticks = max(1, int(seconds / tick))
    for i in range(total_ticks + 1):
        elapsed = min(seconds, i * tick)
        line = codex_working_line(elapsed)
        sys.stdout.write("\r\x1b[2K" + line)
        sys.stdout.flush()
        if i < total_ticks:
            time.sleep(tick)
    sys.stdout.write("\r\x1b[2K")
    sys.stdout.flush()


def claude_working_status_lines(frame: int, started_at: float, verb: str, tip: str) -> list[str]:
    elapsed = max(1, time.time() - started_at)
    tokens = max(1, int(elapsed * 24))
    spinner = FRAMES[frame % len(FRAMES)]
    meta = f"{format_working_elapsed(elapsed)} · ↓ {tokens} tokens · thinking with {EFFORT} effort"
    return [
        f"{CLAUDE_ORANGE}{spinner}{ANSI_RESET} {verb}… {ANSI_DIM}({meta}){ANSI_RESET}",
        f"  ⎿  Tip: {tip}",
    ]


def codex_working_status_lines(frame: int, started_at: float) -> list[str]:
    elapsed = max(1, time.time() - started_at)
    return [codex_working_line(elapsed)]


def agent_working_status_lines(frame: int, started_at: float, verb: str, tip: str) -> list[str]:
    if PERMISSION_STYLE == "codex":
        return codex_working_status_lines(frame, started_at)
    return claude_working_status_lines(frame, started_at, verb, tip)


def write_working_status_block(lines: list[str]) -> None:
    sys.stdout.write("\r\x1b[2K" + lines[0])
    for line in lines[1:]:
        sys.stdout.write("\n\r\x1b[2K" + line)
    if len(lines) > 1:
        sys.stdout.write(f"\x1b[{len(lines) - 1}A")
    sys.stdout.flush()


def finish_working_status_block(lines: list[str]) -> None:
    write_working_status_block(lines)
    if len(lines) > 1:
        sys.stdout.write(f"\x1b[{len(lines) - 1}B")
    sys.stdout.write("\n")
    sys.stdout.flush()


def agent_working_status(stop_event: threading.Event, started_at: float, verb: str, tip: str) -> None:
    frame = 0
    while not stop_event.is_set():
        write_working_status_block(agent_working_status_lines(frame, started_at, verb, tip))
        frame += 1
        stop_event.wait(0.12)


def run_with_agent_working_status(command: str, use_real: bool) -> tuple[str, int]:
    started_at = time.time()
    verb = random.choice(CLAUDE_WORKING_VERBS)
    tip = random.choice(CLAUDE_TIPS)
    if not sys.stdout.isatty():
        print("\n".join(agent_working_status_lines(0, started_at, verb, tip)))
        result = real_exec(command) if use_real else result_for_command(command)
        elapsed = max(1, round(time.time() - started_at))
        return result, elapsed

    stop_event = threading.Event()
    worker = threading.Thread(target=agent_working_status, args=(stop_event, started_at, verb, tip), daemon=True)
    worker.start()
    try:
        result = real_exec(command) if use_real else result_for_command(command)
    finally:
        stop_event.set()
        worker.join(timeout=0.5)
    elapsed = max(1, round(time.time() - started_at))
    finish_working_status_block(agent_working_status_lines(0, started_at, verb, tip))
    return result, elapsed


def print_assistant(text: str) -> None:
    """Assistant turn: ● once on first line, wrapped continuations indented under."""
    width = max(40, terminal_width() - 4)
    paragraphs = text.split("\n\n")
    bullet_used = False
    for pi, para in enumerate(paragraphs):
        if pi > 0:
            print()
        for line in textwrap.wrap(para, width=width) or [""]:
            if not bullet_used:
                print(f"● {line}")
                bullet_used = True
            else:
                print(f"  {line}")
    print()


def normalize_input(user_input: str) -> str:
    value = user_input.strip()
    if value.lower().startswith("user: "):
        return value[6:].strip()
    return value


def question_option_lines(options: list[str], selected: int) -> list[str]:
    return [
        f"  {SELECTOR_GLYPH if i == selected else ' '} {i + 1}. {opt}"
        for i, opt in enumerate(options)
    ]


def print_question(question: str, options: list[str] | None = None) -> None:
    print(f"● {question}")
    if options:
        print()
        for line in question_option_lines(options, 0):
            print(line)
    print()


def redraw_question_options(options: list[str], selected: int) -> None:
    # print_question lays out K options followed by a trailing blank line and
    # leaves the cursor one row below that blank. Move up K+1 rows to land on
    # the first option, clear to end, redraw K options, then re-emit the blank
    # so the cursor lands back where it started.
    n_up = len(options) + 1
    sys.stdout.write(f"\x1b[{n_up}A\x1b[J")
    for line in question_option_lines(options, selected):
        sys.stdout.write(line + "\r\n")
    sys.stdout.write("\r\n")
    sys.stdout.flush()


def set_pending_question(state: dict[str, str], question: str, options: list[str]) -> None:
    state["pending"] = "question"
    state["question"] = question
    state["question_options"] = "\x1f".join(options)
    state["question_selected"] = "0"


def handle_pending_question_tty(state: dict[str, str]) -> None:
    options = state.get("question_options", "").split("\x1f")
    selected = int(state.get("question_selected", "0"))
    old_settings = termios.tcgetattr(sys.stdin.fileno())
    chosen: int | None = None
    try:
        tty.setraw(sys.stdin.fileno())
        while state.get("pending") == "question":
            key = read_key()
            if key in {"\x1b[A", "\x1bOA", "\x10", "k"}:
                if selected > 0:
                    selected -= 1
                    state["question_selected"] = str(selected)
                    redraw_question_options(options, selected)
            elif key in {"\x1b[B", "\x1bOB", "\x0e", "j"}:
                if selected < len(options) - 1:
                    selected += 1
                    state["question_selected"] = str(selected)
                    redraw_question_options(options, selected)
            elif key in {"\r", "\n"}:
                chosen = selected
                break
            elif key.isdigit():
                idx = int(key) - 1
                if 0 <= idx < len(options):
                    chosen = idx
                    break
            elif key == "\x1b":
                break
            elif key == "\x03":
                break
    finally:
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old_settings)
    state.pop("question", None)
    state.pop("question_options", None)
    state.pop("question_selected", None)
    state["pending"] = ""
    print()
    if chosen is None:
        print("● Cancelled.")
    else:
        print(f"● You picked: {options[chosen]}")
    print()


def preview_verb(command: str) -> str:
    c = command.strip().lower()
    if c.startswith("sleep ") or c.startswith("wait "):
        return "Sleeping"
    if c.startswith("ls") or c.startswith("find ") or "list" in c.split()[:2]:
        return "Listing"
    if c.startswith(("cat ", "less ", "head ", "tail ")):
        return "Reading"
    if c.startswith(("grep", "rg ")):
        return "Searching"
    if c.startswith("git "):
        return "Running git"
    if c.startswith(("tmux ", "docker ")):
        return "Inspecting"
    return "Running"


def print_bash_prompt(command: str, description: str = "Run shell command") -> int:
    """Render the Bash permission block. Returns total newlines emitted so
    the approval/cancel handler can erase exactly this block."""
    # A command awaiting approval is NOT running yet: real Claude shows ONLY the
    # permission block here. The `● Bash(...)`/Running/result render happens AFTER
    # approval (see approve_pending_permission). Emitting no `⎿ Running…` working
    # line is also what lets the auto-approve detector see a clean LIVE prompt
    # instead of mistaking the screen for "agent working" and skipping it.
    cmd_lines = textwrap.wrap(command, width=76) or [""]
    n = 0
    prompt_rule(terminal_width()); n += 1
    print(); n += 1
    if PERMISSION_STYLE == "codex":
        print(f"◦ {preview_verb(command)} {command}"); n += 1
        print(); n += 1
        print(); n += 1
        print("  Would you like to run the following command?"); n += 1
        print(); n += 1
        for i, line in enumerate(cmd_lines):
            print(f"  {'$ ' if i == 0 else '  '}{line}"); n += 1
        print(); n += 1
    else:
        print(" Bash command (unsandboxed)"); n += 1
        print(); n += 1
        for line in cmd_lines:
            print(f"   {line}"); n += 1
        print(f"   {description}"); n += 1
        print(); n += 1
        print(" Permission rule Bash requires confirmation for this command."); n += 1
        print(); n += 1
    print_permission_choices(0, command); n += len(permission_choice_lines(0, command))
    return n


def print_done_summary(seconds: int) -> None:
    """Real Claude prints `● Done.` + `* <PastVerb> for Ns` after a tool finishes."""
    print("● Done.")
    print()
    print(f"* {random.choice(PAST_VERBS)} for {max(1, seconds)}s")
    print()


def print_tool_result(command: str, result: str = "ok") -> None:
    print(f"● Bash({command})")
    lines = result.split("\n") if result else [""]
    for i, line in enumerate(lines):
        prefix = "  ⎿  " if i == 0 else "     "
        print(f"{prefix}{line}")
    print()
    print_done_summary(seconds=random.randint(2, 9))


def print_tool_error(command: str) -> None:
    print(f"● Bash({command})")
    print("  ⎿  Error: Exit code 1")
    print(f"     bwrap: Can't create file at {os.path.expanduser('~')}/.{AGENT_NAME}/skills: Is a directory")
    print()


def print_tool_multiline(command: str, lines: list[str], more: int = 0) -> None:
    print(f"● Bash({command})")
    for index, line in enumerate(lines):
        prefix = "  ⎿  " if index == 0 else "     "
        print(f"{prefix}{line}")
    if more:
        print(f"     … +{more} lines (ctrl+o to expand)")
    print()


def print_confirm_menu(title: str, body: str, options: list[tuple[str, str]]) -> None:
    width = terminal_width()
    prompt_rule(width)
    print(" ☐ Confirm ")
    print()
    print(body)
    print()
    for index, (label, detail) in enumerate(options, start=1):
        marker = SELECTOR_GLYPH if index == 1 else " "
        print(f"{marker} {index}. {label}")
        if detail:
            print(f"     {detail}")
    prompt_rule(width)
    print(f"  {len(options) + 1}. Chat about this")
    print()


def set_pending_permission(state: dict[str, str], command: str, description: str, lines: int = 0) -> None:
    state["pending"] = "permission"
    state["command"] = command
    state["description"] = description
    state["selected"] = "0"
    state["prompt_lines"] = str(lines)


def clear_pending(state: dict[str, str]) -> None:
    # Preserve session counters AND any in-flight yesno sequence — approve/cancel call
    # clear_pending between steps, and advance_yesno_queue needs the queue to survive.
    keep = {k: v for k, v in state.items()
            if k in {"tokens_in", "tokens_out", "turn",
                     "yesno_total", "yesno_idx", "yesno_queue"}}
    state.clear()
    state.update(keep)


# Realistic-looking (but mock-executed — no real side effects) unix commands a build/create
# script would run, each behind its own Yes/No permission prompt. `yesno N` queues N of these.
YESNO_STEPS = [
    ("mkdir -p build/output", "Create the build output directory"),
    ("chmod +x scripts/deploy.sh", "Make the deploy script executable"),
    ("cp -r src/ dist/", "Copy sources into dist/"),
    ("rm -f dist/*.tmp", "Remove temporary build files"),
    ("git add -A", "Stage all changes"),
    ("tar czf release.tgz dist/", "Create the release archive"),
    ("npm ci", "Install pinned npm dependencies"),
    ("docker build -t mockapp:latest .", "Build the container image"),
    ("sed -i 's/0.0.0/1.0.0/' VERSION", "Bump the version string"),
    ("git commit -m 'mock build'", "Commit the build"),
]


def clear_yesno(state: dict[str, str]) -> None:
    for key in ("yesno_total", "yesno_idx", "yesno_queue"):
        state.pop(key, None)


def start_yesno_step(state: dict[str, str], command: str, description: str) -> None:
    idx = state.get("yesno_idx", "1")
    total = state.get("yesno_total", "1")
    label = f"[{idx}/{total}] {description}" if description else f"[{idx}/{total}]"
    n = print_bash_prompt(command, label)
    set_pending_permission(state, command, label, lines=n)


def cmd_yesno(state: dict[str, str], count: int) -> None:
    """Queue COUNT mock-build steps, each asking Yes/No. Approving advances to the
    next step; declining aborts the rest. Useful for exercising auto-approve across
    several consecutive prompts."""
    count = max(1, min(count, 50))
    steps = [YESNO_STEPS[i % len(YESNO_STEPS)] for i in range(count)]
    plural = "s" if count != 1 else ""
    print(f"● Mock build script — {count} step{plural}, each needs Yes/No.")
    print()
    state["yesno_total"] = str(count)
    state["yesno_idx"] = "1"
    state["yesno_queue"] = "\n".join(f"{cmd}\t{desc}" for cmd, desc in steps[1:])
    first_cmd, first_desc = steps[0]
    start_yesno_step(state, first_cmd, first_desc)


def advance_yesno_queue(state: dict[str, str], approved: bool) -> None:
    """After a permission prompt resolves, drive the next yesno step (or finish).
    No-op when there is no active yesno sequence (a plain single prompt)."""
    if "yesno_total" not in state:
        return
    total = int(state.get("yesno_total", "1"))
    idx = int(state.get("yesno_idx", "1"))
    queue_raw = state.get("yesno_queue", "")
    remaining = [line for line in queue_raw.split("\n") if line] if queue_raw else []
    if not approved:
        skipped = total - idx
        plural = "s" if skipped != 1 else ""
        print(f"● Build script aborted at step {idx}/{total} ({skipped} step{plural} skipped).")
        print()
        clear_yesno(state)
        return
    if not remaining:
        print(f"● Build script complete — {total}/{total} steps approved.")
        print()
        clear_yesno(state)
        return
    cmd, _, desc = remaining[0].partition("\t")
    state["yesno_idx"] = str(idx + 1)
    state["yesno_queue"] = "\n".join(remaining[1:])
    # `ask N` cadence: think briefly (~1s) between asks so the sequence looks like a real agent working
    # through steps — ask -> think -> ask -> think -> … — not a burst of back-to-back prompts. The
    # spinner erases itself, so the next prompt is still the clean bottom-of-screen block the detector
    # needs (it reads `working` during the think, then `approval` once the prompt renders).
    print_thinking(seconds=1)
    start_yesno_step(state, cmd, desc)


def reusable_command_prefix(command: str) -> str:
    words = command.strip().split()
    if len(words) >= 2 and words[0] == "gh" and words[1] == "api":
        return "gh api"
    return " ".join(words[:2]) if len(words) >= 2 else (words[0] if words else "command")


def permission_choice_lines(selected: int, command: str | None = None) -> list[str]:
    if PERMISSION_STYLE == "codex":
        prefix = reusable_command_prefix(command or "")
        markers = [SELECTOR_GLYPH if selected == i else " " for i in range(3)]
        return [
            f"{markers[0]} 1. Yes, proceed (y)",
            f"{markers[1]} 2. Yes, and don't ask again for commands that start with `{prefix}` (p)",
            f"{markers[2]} 3. No, and tell Codex what to do differently (esc)",
            "",
            "  Press enter to confirm or esc to cancel",
            "",
        ]
    yes_marker = SELECTOR_GLYPH if selected == 0 else " "
    no_marker = SELECTOR_GLYPH if selected == 1 else " "
    return [
        " Do you want to proceed?",
        f" {yes_marker} 1. Yes",
        f" {no_marker} 2. No",
        "",
        " Esc to cancel",
        "",
    ]


def print_permission_choices(selected: int, command: str | None = None) -> None:
    for line in permission_choice_lines(selected, command):
        print(line)


def redraw_permission_choices(selected: int, command: str | None = None) -> None:
    sys.stdout.write("\x1b[6A\x1b[J")
    sys.stdout.write("\r\n".join(permission_choice_lines(selected, command)))
    sys.stdout.write("\r\n")
    sys.stdout.flush()


def erase_prompt_block(state: dict[str, str]) -> None:
    """Erase exactly the permission block printed by print_bash_prompt. Banner
    and prior conversation above are preserved. Required so YOLO's
    `tmux capture-pane -p` no longer sees 'Do you want to proceed?' after
    approval, avoiding the re-fire loop."""
    if not sys.stdout.isatty():
        return
    n = int(state.get("prompt_lines", "0"))
    if n <= 0:
        return
    sys.stdout.write(f"\x1b[{n}A\x1b[J")
    sys.stdout.flush()


def approve_pending_permission(state: dict[str, str]) -> None:
    command = state.get("command", "echo ok")
    use_real = state.get("real_exec") == "1"
    erase_prompt_block(state)
    clear_pending(state)
    print(f"● User approved {AGENT_DISPLAY_NAME}'s request")
    print()
    result, elapsed = run_with_agent_working_status(command, use_real)
    print(f"● Bash({command})")
    result_lines = result.split("\n") if result else [""]
    for i, line in enumerate(result_lines):
        prefix = "  ⎿  " if i == 0 else "     "
        print(f"{prefix}{line}")
    print()
    print_done_summary(seconds=elapsed)
    advance_yesno_queue(state, approved=True)


def cancel_pending_permission(state: dict[str, str]) -> None:
    erase_prompt_block(state)
    clear_pending(state)
    print("● Cancelled.")
    print()
    advance_yesno_queue(state, approved=False)


def _has_more(timeout: float = 0.05) -> bool:
    # Poll the OS-level fd so we bypass Python's TextIOWrapper buffer, which
    # would otherwise hide bytes that have already been slurped from the OS
    # (causing arrow sequences like \x1b[B to be mis-detected as bare Esc).
    ready, _, _ = select.select([sys.stdin.fileno()], [], [], timeout)
    return bool(ready)


def _read_byte() -> str:
    return os.read(sys.stdin.fileno(), 1).decode("utf-8", errors="replace")


def read_key() -> str:
    char = _read_byte()
    if char != "\x1b":
        return char
    if not _has_more():
        return char
    second = _read_byte()
    if second in {"[", "O"}:
        if not _has_more():
            return char + second
        third = _read_byte()
        return char + second + third
    return char + second


def claude_mode_index(state: dict[str, str] | None) -> int:
    if state is None:
        return 0
    try:
        index = int(state.get("claude_mode_index", "0") or 0)
    except ValueError:
        index = 0
    return index % len(CLAUDE_MODE_STATUS_LINES)


def claude_mode_status_line(state: dict[str, str] | None) -> str:
    return CLAUDE_MODE_STATUS_LINES[claude_mode_index(state)]


def cycle_claude_mode(state: dict[str, str]) -> None:
    state["claude_mode_index"] = str((claude_mode_index(state) + 1) % len(CLAUDE_MODE_STATUS_LINES))


def live_composer_status_line(armed_exit: bool = False, state: dict[str, str] | None = None) -> str:
    # After the first Ctrl-C, real Claude replaces the status hint with this exact
    # text until the next key (a second Ctrl-C then exits).
    if armed_exit:
        return "Press Ctrl-C again to exit"
    if PERMISSION_STYLE != "codex":
        return claude_mode_status_line(state)
    return f"{MODEL} {EFFORT} · {display_cwd()}"


def live_composer_suggestion() -> str:
    if PERMISSION_STYLE == "codex":
        return "Write tests for @filename"
    return 'Try "fix typecheck errors"'


def live_composer_rows() -> tuple[int, int]:
    height = terminal_height()
    if height <= 1:
        return 1, 1
    if height == 2:
        return 1, 2
    return height - 2, height


def live_composer_separator_rows() -> list[int]:
    prompt_row, status_row = live_composer_rows()
    rows: list[int] = []
    if prompt_row > 1:
        rows.append(prompt_row - 1)
    if status_row - prompt_row > 1:
        rows.append(status_row - 1)
    return rows


def live_composer_separator_line() -> str:
    return ANSI_DIM + ("─" * terminal_width()) + ANSI_RESET


def composer_render_parts(text: str, cursor: int, armed_exit: bool = False, state: dict[str, str] | None = None) -> tuple[str, str, int]:
    width = terminal_width()
    prefix = f"{PROMPT_GLYPH} "
    text_width = max(1, width - len(prefix) - 1)
    cursor = max(0, min(len(text), cursor))
    start = max(0, cursor - text_width)
    if cursor < start:
        start = cursor
    visible = text[start:start + text_width]
    if text:
        prompt_display = prefix + visible
    else:
        prompt_display = prefix + ANSI_DIM + live_composer_suggestion()[:text_width] + ANSI_RESET
    cursor_col = min(width, len(prefix) + (cursor - start) + 1)
    status_display = clipped(live_composer_status_line(armed_exit, state), width)
    return prompt_display, status_display, cursor_col


def render_live_composer(text: str, cursor: int, armed_exit: bool = False, state: dict[str, str] | None = None) -> None:
    prompt_row, status_row = live_composer_rows()
    prompt_display, status_display, cursor_col = composer_render_parts(text, cursor, armed_exit, state)
    separator = live_composer_separator_line()
    for row in live_composer_separator_rows():
        sys.stdout.write(f"\x1b[{row};1H\x1b[2K{separator}")
    sys.stdout.write(f"\x1b[{prompt_row};1H\x1b[2K{prompt_display}")
    sys.stdout.write(f"\x1b[{status_row};1H\x1b[2K{status_display}")
    sys.stdout.write(f"\x1b[{prompt_row};{cursor_col}H")
    sys.stdout.flush()


def render_inline_composer(text: str, cursor: int, armed_exit: bool = False) -> None:
    prompt_display, status_display, cursor_col = composer_render_parts(text, cursor, armed_exit)
    sys.stdout.write("\r\x1b[2K" + prompt_display)
    sys.stdout.write("\n\r\x1b[2K")
    sys.stdout.write("\n\r\x1b[2K" + status_display)
    sys.stdout.write(f"\x1b[2A\x1b[{cursor_col}G")
    sys.stdout.flush()


def clear_live_composer() -> None:
    prompt_row, status_row = live_composer_rows()
    for row in live_composer_separator_rows():
        sys.stdout.write(f"\x1b[{row};1H\x1b[2K")
    sys.stdout.write(f"\x1b[{prompt_row};1H\x1b[2K")
    sys.stdout.write(f"\x1b[{status_row};1H\x1b[2K")
    sys.stdout.write(f"\x1b[{prompt_row};1H")
    sys.stdout.flush()


def clear_inline_composer() -> None:
    sys.stdout.write("\r\x1b[2K\n\r\x1b[2K\n\r\x1b[2K\x1b[2A\r")
    sys.stdout.flush()


def finish_inline_composer(text: str) -> None:
    if text.strip():
        prefix = f"{PROMPT_GLYPH} "
        width = terminal_width()
        visible = text[: max(1, width - len(prefix))]
        sys.stdout.write("\r\x1b[2K" + prefix + visible)
        sys.stdout.write("\n\r\x1b[2K\n\r\x1b[2K\n")
    else:
        clear_inline_composer()
    sys.stdout.flush()


def history_item(index: int) -> str:
    return readline.get_history_item(index) or ""


def read_live_composer(state: dict[str, str] | None = None) -> str:
    text = ""
    cursor = 0
    history_count = readline.get_current_history_length()
    history_index = history_count + 1
    draft = ""
    inline_composer = PERMISSION_STYLE == "codex"
    old_settings = termios.tcgetattr(sys.stdin.fileno())
    try:
        tty.setraw(sys.stdin.fileno())
        while True:
            armed_exit = bool(state and state.get("last_ctrl_c_at"))
            if inline_composer:
                render_inline_composer(text, cursor, armed_exit)
            else:
                render_live_composer(text, cursor, armed_exit, state)
            key = read_key()
            if state is not None and key != "\x03":
                clear_ctrl_c_exit_window(state)
            if key == "\x1b[Z" and state is not None and not inline_composer:
                cycle_claude_mode(state)
                continue
            if key in {"\r", "\n"}:
                if inline_composer:
                    finish_inline_composer(text)
                else:
                    clear_live_composer()
                if text.strip():
                    readline.add_history(text)
                return text
            if key == "\x03":
                if inline_composer:
                    clear_inline_composer()
                else:
                    clear_live_composer()
                raise KeyboardInterrupt
            if key == "\x04":
                if text:
                    text = text[:cursor] + text[cursor + 1:]
                    continue
                if inline_composer:
                    clear_inline_composer()
                else:
                    clear_live_composer()
                raise EOFError
            if key in {"\x7f", "\b"}:
                if cursor > 0:
                    text = text[:cursor - 1] + text[cursor:]
                    cursor -= 1
                continue
            if key in {"\x1b[D", "\x1bOD", "\x02"}:
                cursor = max(0, cursor - 1)
                continue
            if key in {"\x1b[C", "\x1bOC", "\x06"}:
                cursor = min(len(text), cursor + 1)
                continue
            if key == "\x01":
                cursor = 0
                continue
            if key == "\x05":
                cursor = len(text)
                continue
            if key == "\x0b":
                text = text[:cursor]
                continue
            if key == "\x15":
                text = text[cursor:]
                cursor = 0
                continue
            if key == "\x17":
                start = cursor
                while start > 0 and text[start - 1].isspace():
                    start -= 1
                while start > 0 and not text[start - 1].isspace():
                    start -= 1
                text = text[:start] + text[cursor:]
                cursor = start
                continue
            if key in {"\x1b[A", "\x1bOA", "\x10"} and history_count:
                if history_index == history_count + 1:
                    draft = text
                history_index = max(1, history_index - 1)
                text = history_item(history_index)
                cursor = len(text)
                continue
            if key in {"\x1b[B", "\x1bOB", "\x0e"} and history_count:
                if history_index <= history_count:
                    history_index += 1
                text = draft if history_index == history_count + 1 else history_item(history_index)
                cursor = len(text)
                continue
            if len(key) == 1 and key >= " ":
                text = text[:cursor] + key + text[cursor:]
                cursor += len(key)
    finally:
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old_settings)


def handle_pending_permission_tty(state: dict[str, str]) -> None:
    selected = int(state.get("selected", "0"))
    choice_count = 3 if PERMISSION_STYLE == "codex" else 2
    old_settings = termios.tcgetattr(sys.stdin.fileno())
    action = ""
    try:
        tty.setraw(sys.stdin.fileno())
        while state.get("pending") == "permission":
            key = read_key()
            if key in {"\x1b[A", "\x1bOA", "\x10", "k"}:
                selected = max(0, selected - 1)
                state["selected"] = str(selected)
                redraw_permission_choices(selected, state.get("command"))
            elif key in {"\x1b[B", "\x1bOB", "\x0e", "j"}:
                selected = min(choice_count - 1, selected + 1)
                state["selected"] = str(selected)
                redraw_permission_choices(selected, state.get("command"))
            elif key in {"\r", "\n"}:
                action = "cancel" if selected == choice_count - 1 else "approve"
                break
            elif key in {"1", "y", "Y"}:
                action = "approve"
                break
            elif PERMISSION_STYLE == "codex" and key == "2":
                action = "approve"
                break
            elif key == str(choice_count) or key in {"n", "N"}:
                action = "cancel"
                break
            elif key == "\x1b":
                action = "cancel"
                break
            elif key == "\x03":
                action = "cancel"
                break
    finally:
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old_settings)
    if action:
        print()
        if action == "approve":
            approve_pending_permission(state)
        else:
            cancel_pending_permission(state)


def handle_pending_input(user_input: str, state: dict[str, str]) -> bool:
    pending = state.get("pending")
    if pending == "permission":
        value = user_input.lower()
        if value in {"", "1", "yes", "y"}:
            approve_pending_permission(state)
            return True
        if PERMISSION_STYLE == "codex" and value == "2":
            approve_pending_permission(state)
            return True
        cancel_values = {"no", "n", "cancel", "3"} if PERMISSION_STYLE == "codex" else {"2", "no", "n", "cancel"}
        if value in cancel_values:
            cancel_pending_permission(state)
            return True
        return False
    return False


def real_exec(command: str, timeout: int = 300) -> str:
    """Run command in a real shell, return combined stdout+stderr (rstripped)."""
    try:
        proc = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return f"Error: Timed out after {timeout}s"
    out = (proc.stdout or "") + (proc.stderr or "")
    out = out.rstrip("\n")
    if proc.returncode != 0:
        head = f"Error: Exit code {proc.returncode}"
        return f"{head}\n{out}" if out else head
    return out if out else "(No output)"


def result_for_command(command: str) -> str:
    if "tmux rename-session" in command:
        return "\n     1: 2 windows (created Thu May  7 14:34:50 2026) (attached)\n     2: 2 windows (created Wed May  6 13:46:17 2026) (attached)\n     3: 2 windows (created Fri May  8 12:41:25 2026) (attached)\n     … +2 lines (ctrl+o to expand)"
    match = re.match(r"^\s*sleep\s+(\d+(?:\.\d+)?)\s*(?:&&\s*echo\s+(.+))?\s*$", command)
    if match:
        time.sleep(float(match.group(1)))
        echoed = match.group(2)
        return echoed.strip() if echoed else "(No output)"
    if "tmux ls" in command:
        return "1: 1 windows (created Tue May 26 07:42:56 2026)"
    return "ok"


def session_increment_tokens(state: dict[str, str], delta: int = 39) -> None:
    state["tokens_in"] = str(int(state.get("tokens_in", "0")) + max(1, delta // 3))
    state["tokens_out"] = str(int(state.get("tokens_out", "0")) + delta)


def codex_session_id(state: dict[str, str]) -> str:
    if not state.get("codex_session_id"):
        state["codex_session_id"] = str(uuid.uuid4())
    return state["codex_session_id"]


def codex_token_usage_line(state: dict[str, str]) -> str:
    input_tokens = 22552 + int(state.get("tokens_in", "0") or 0)
    output_tokens = 904 + int(state.get("tokens_out", "0") or 0)
    cached_tokens = 66688 + max(0, int(state.get("tokens_in", "0") or 0) * 3)
    reasoning_tokens = min(output_tokens, 593 + max(0, int(state.get("tokens_out", "0") or 0) // 2))
    total_tokens = input_tokens + output_tokens
    return (
        f"Token usage: total={total_tokens:,} input={input_tokens:,} "
        f"(+ {cached_tokens:,} cached) output={output_tokens:,} "
        f"(reasoning {reasoning_tokens:,})"
    )


def print_codex_exit_footer(state: dict[str, str]) -> None:
    print(codex_token_usage_line(state))
    print(f"To continue this session, run codex resume {codex_session_id(state)}")


def print_exit_message(state: dict[str, str]) -> None:
    if AGENT_NAME == "codex":
        print_codex_exit_footer(state)
    else:
        print("● Goodbye.")


def mock_fixture_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").lower()).strip("_")


def mock_fixture_path(inventory_path: Path, file_name: object) -> Path:
    value = str(file_name or "")
    if inventory_path.parent.name == "captures":
        return inventory_path.parent / value
    return PROMPT_CORPUS_DIR / value


def fixture_agent_name(data: dict[str, object], inventory_item: dict[str, object]) -> str:
    expected = inventory_item.get("expected") if isinstance(inventory_item.get("expected"), dict) else {}
    return str(data.get("agent") or expected.get("agent") or "")


def fixture_expected_metadata(data: dict[str, object], inventory_item: dict[str, object]) -> dict[str, object]:
    expected: dict[str, object] = {}
    for source in (data.get("expected"), data.get("expected_promoted"), inventory_item.get("expected")):
        if isinstance(source, dict):
            expected.update(source)
    return expected


def fixture_case_names(data: dict[str, object], inventory_item: dict[str, object], path: Path) -> set[str]:
    names = {
        data.get("case_name"),
        data.get("fixture_scenario"),
        data.get("fixture_id"),
        data.get("id"),
        inventory_item.get("case_name"),
        inventory_item.get("scenario"),
        inventory_item.get("fixture_id"),
        inventory_item.get("id"),
        inventory_item.get("family"),
        path.stem,
    }
    agent = fixture_agent_name(data, inventory_item)
    keys = {mock_fixture_key(name) for name in names if name}
    if agent:
        keys.update({mock_fixture_key(f"{agent}_{name}") for name in names if name})
    return {key for key in keys if key}


def load_mock_fixture_cases() -> list[dict[str, object]]:
    global MOCK_FIXTURE_CASES
    if MOCK_FIXTURE_CASES is not None:
        return MOCK_FIXTURE_CASES
    cases: list[dict[str, object]] = []
    for inventory_path in [PROMPT_CORPUS_DIR / "captures" / "inventory.yaml", PROMPT_CORPUS_DIR / "inventory.yaml"]:
        inventory = yaml.safe_load(inventory_path.read_text(encoding="utf-8")) or {}
        for inventory_item in inventory.get("fixtures", []):
            if not isinstance(inventory_item, dict):
                continue
            path = mock_fixture_path(inventory_path, inventory_item.get("file"))
            if not path.exists():
                continue
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if not isinstance(data, dict):
                continue
            styled_capture = str(data.get("styled_capture") or data.get("raw_capture") or data.get("visible_text") or "")
            raw_capture = str(data.get("raw_capture") or data.get("visible_text") or "")
            cursor = data.get("cursor") if isinstance(data.get("cursor"), dict) else {}
            cases.append({
                "agent": fixture_agent_name(data, inventory_item),
                "case_name": str(data.get("case_name") or inventory_item.get("case_name") or inventory_item.get("scenario") or path.stem),
                "keys": fixture_case_names(data, inventory_item, path),
                "path": path,
                "raw_capture": raw_capture,
                "styled_capture": styled_capture,
                "cursor": cursor,
                "expected": fixture_expected_metadata(data, inventory_item),
            })
    MOCK_FIXTURE_CASES = cases
    return cases


def find_mock_fixture_case(name: str) -> dict[str, object] | None:
    key = mock_fixture_key(name)
    if not key:
        return None
    matches = [case for case in load_mock_fixture_cases() if key in case["keys"]]
    current_agent_matches = [case for case in matches if case.get("agent") == AGENT_NAME]
    if current_agent_matches:
        return current_agent_matches[0]
    return matches[0] if matches else None


def mock_fixture_cursor_label(cursor: dict[str, object]) -> str:
    if "x" in cursor and "y" in cursor:
        return f"cursor={int(cursor.get('x') or 0)},{int(cursor.get('y') or 0)}"
    if cursor.get("error"):
        return f"cursor=error:{cursor.get('error')}"
    return "cursor=missing"


def mock_fixture_prompt_cursor(lines: list[str], group: dict[str, object] | None = None) -> tuple[int, int] | None:
    if group:
        idxs = group.get("idxs")
        selected = int(group.get("selected") or 0)
        if isinstance(idxs, list) and 0 <= selected < len(idxs):
            row_index = int(idxs[selected])
            line = lines[row_index] if 0 <= row_index < len(lines) else ""
            for glyph in (str(group.get("glyph") or ""), SELECTOR_GLYPH, "❯", "›", ">"):
                if glyph and glyph in line:
                    return line.index(glyph), row_index

    status_index = next((index for index in range(len(lines) - 1, -1, -1) if lines[index].strip()), len(lines))
    for row_index in range(status_index - 1, -1, -1):
        line = lines[row_index]
        stripped = line.lstrip()
        for glyph in (PROMPT_GLYPH, "❯", "›", ">"):
            if stripped.startswith(glyph):
                leading = len(line) - len(stripped)
                after_glyph = stripped[len(glyph):]
                x = leading + len(glyph) + (1 if after_glyph.startswith(" ") else 0)
                return x, row_index
    return None


def mock_fixture_render_cursor(lines: list[str], cursor: dict[str, object], height: int, top_padding: int, group: dict[str, object] | None = None, drop: int = 0) -> tuple[int, int] | None:
    if "x" in cursor and "y" in cursor:
        # Recorded x/y are in the ORIGINAL capture's coordinates; `drop` top lines
        # were trimmed to fit the pane, so shift y up by that much and clamp x to width.
        x = min(terminal_width() - 1, int(cursor.get("x") or 0))
        y = max(0, int(cursor.get("y") or 0) - drop)
        return x, min(height - 1, y + top_padding)
    inferred = mock_fixture_prompt_cursor(lines, group)
    if inferred is None:
        return None
    x, row_index = inferred
    return x, min(height - 1, row_index + top_padding)


def mock_fixture_list_relevant(case: dict[str, object]) -> bool:
    agent = str(case.get("agent") or "").strip().lower()
    if not agent or agent in {"unknown", "generic"}:
        return True
    return agent == AGENT_NAME


def mock_fixture_list_key(case: dict[str, object]) -> tuple[str, str, str]:
    path = Path(case.get("path") or "")
    return (
        str(case.get("agent") or "").strip().lower() or "generic",
        str(case.get("case_name") or "").strip().lower(),
        str(path),
    )


def mock_fixture_label_key(case: dict[str, object]) -> tuple[str, str]:
    return (
        str(case.get("agent") or "").strip().lower() or "generic",
        str(case.get("case_name") or "").strip().lower(),
    )


def mock_fixture_is_real_capture(case: dict[str, object]) -> bool:
    return Path(case.get("path") or "").parent.name == "captures"


def mock_fixture_outcome_label(case: dict[str, object]) -> str:
    expected = case.get("expected") if isinstance(case.get("expected"), dict) else {}
    attention_label = str(expected.get("attention_label") or "").strip()
    if attention_label:
        return attention_label
    screen_key = str(expected.get("screen_key") or "").strip()
    reason_code = str(expected.get("reason_code") or "").strip()
    composer_key = str(expected.get("composer_key") or "").strip()
    if expected.get("approval_visible") is True or screen_key == "approval":
        return "YOLO?"
    if expected.get("ask") is True or screen_key == "needs-input":
        return "ASK?"
    if screen_key == "working" or reason_code == "busy":
        return "RUN"
    if screen_key == "input-draft" or composer_key == "draft":
        return "draft"
    if screen_key == "idle" or reason_code == "idle":
        return "idle"
    if composer_key == "ghost":
        return "ghost"
    return "unknown"


def mock_fixture_allows_choice_interaction(case: dict[str, object]) -> bool:
    expected = case.get("expected") if isinstance(case.get("expected"), dict) else {}
    if not expected:
        return True
    screen_key = str(expected.get("screen_key") or "").strip()
    return expected.get("ask") is True or expected.get("approval_visible") is True or screen_key in {"approval", "needs-input"}


def mock_fixture_list_cases() -> list[dict[str, object]]:
    seen: set[tuple[str, str, str]] = set()
    visible_cases: list[dict[str, object]] = []
    sorted_cases = sorted(load_mock_fixture_cases(), key=lambda c: (str(c.get("case_name") or "").lower(), str(c.get("agent") or ""), str(c.get("path") or "")))
    relevant_cases = [case for case in sorted_cases if mock_fixture_list_relevant(case)]
    real_labels = {mock_fixture_label_key(case) for case in relevant_cases if mock_fixture_is_real_capture(case)}
    for case in relevant_cases:
        if not mock_fixture_is_real_capture(case) and mock_fixture_label_key(case) in real_labels:
            continue
        key = mock_fixture_list_key(case)
        if key in seen:
            continue
        seen.add(key)
        visible_cases.append(case)
    return visible_cases


def print_mock_fixture_list() -> None:
    print("● Mock fixture cases")
    for case in mock_fixture_list_cases():
        agent = str(case.get("agent") or "generic")
        outcome = mock_fixture_outcome_label(case)
        cursor = case.get("cursor") if isinstance(case.get("cursor"), dict) else {}
        print(f"  ⎿  {agent}: [{outcome}] {case['case_name']} ({Path(case['path']).name}) {mock_fixture_cursor_label(cursor)}")
    print()


_OSC_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_CSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def clip_display_width(line: str, width: int) -> str:
    """Clip a captured line to `width` VISIBLE columns for fitting into the pane.

    Naive `line[:width]` is unsafe: captures carry escape sequences, and cutting an
    OSC-8 hyperlink (\\x1b]8;…\\x1b\\\\) mid-sequence makes the terminal swallow every
    following line. So drop OSC sequences entirely (zero-width, decorative) and keep
    CSI color escapes whole while counting only printable columns toward `width`."""
    line = _OSC_RE.sub("", line)
    out: list[str] = []
    visible = 0
    had_csi = False
    i, n = 0, len(line)
    while i < n and visible < width:
        match = _CSI_RE.match(line, i)
        if match:
            out.append(match.group())
            had_csi = True
            i = match.end()
            continue
        ch = line[i]
        if ch == "\x1b":  # stray/unterminated escape — drop it rather than leak state
            i += 1
            continue
        out.append(ch)
        visible += 1
        i += 1
    if had_csi:
        out.append("\x1b[0m")  # close any color left open by the cut so it can't bleed down
    return "".join(out)


def cmd_mock_fixture(state: dict[str, str], name: str, freeze_static: bool = False) -> None:
    case = find_mock_fixture_case(name)
    if case is None:
        print(f"● Unknown mock fixture case: {name}")
        print()
        print_mock_fixture_list()
        return
    capture = str(case.get("styled_capture") or "")
    cursor = case.get("cursor") if isinstance(case.get("cursor"), dict) else {}
    width = terminal_width()
    height = terminal_height()
    # Fit the captured frame to the ACTUAL pane. Captures are recorded at a fixed
    # size (often 120-200 cols, 40 rows); on a smaller pane the long lines WRAP and
    # the tall frame SCROLLS, and either one desyncs the absolute-row math the
    # interactive redraw relies on — that is what duplicated/garbled the options.
    #   - clip each line to `width` chars     -> no wrap (1 logical line == 1 screen row)
    #   - keep only the bottom `height` lines -> no scroll (the live prompt sits at the
    #     bottom); `drop` records how many top lines were removed so cursor math agrees.
    raw_lines = capture.splitlines()
    clipped_lines = [clip_display_width(line, width) for line in raw_lines]
    drop = max(0, len(clipped_lines) - height)
    lines = clipped_lines[drop:]
    line_count = len(lines)
    # If this capture is a live numbered-choice prompt, let the user actually drive it
    # (arrow keys / digits / Enter / Esc) instead of just freezing the pane. We only do
    # so when the choice block round-trips byte-for-byte under our selector rewrite, so
    # the initial frame stays identical to the capture.
    group = fixture_choice_group(lines) if (sys.stdin.isatty() and mock_fixture_allows_choice_interaction(case)) else None
    # A frozen (mockcase) or interactive (choice) fixture takes over the whole pane, so
    # bottom-align it. A plain `mock <case>` of a NON-choice fixture instead prints inline
    # and hands control back to the live prompt, so TOP-align it — otherwise the composer
    # renders over (and hides) the fixture's bottom rows, e.g. the "2. No" line of
    # command_output_question.
    occupies_screen = bool(group) or freeze_static
    top_padding = max(0, height - line_count) if (occupies_screen and line_count and line_count < height) else 0
    if occupies_screen:
        sys.stdout.write("\x1b[H\x1b[J")
        if top_padding:
            sys.stdout.write("\n" * top_padding)
    elif line_count:
        sys.stdout.write("\n")
    # Render WITHOUT a trailing newline: a trailing "\n" on the bottom-most row
    # scrolls the whole frame up one line, which would throw off the absolute row
    # math the interactive handler uses to move the selector.
    sys.stdout.write("\n".join(lines))
    if not occupies_screen and line_count:
        sys.stdout.write("\n")
    state["fixture_case"] = str(case.get("case_name") or name)
    render_cursor = mock_fixture_render_cursor(lines, cursor, height, top_padding, group, drop)
    if group:
        rows = [top_padding + idx + 1 for idx in group["idxs"]]
        indents = [str(indent) for indent in group["indents"]]
        state["fixture_interactive"] = "1"
        state["pending"] = "fixture"
        state["fixture_option_rows"] = ",".join(str(r) for r in rows)
        state["fixture_option_indents"] = "\x1f".join(indents)
        state["fixture_option_bodies"] = "\x1f".join(group["bodies"])
        state["fixture_selected"] = str(group["selected"])
        state["fixture_glyph"] = str(group["glyph"])
        state["fixture_bottom_row"] = str(top_padding + line_count)
        if render_cursor:
            state["fixture_park_col"] = str(render_cursor[0] + 1)
            state["fixture_park_row"] = str(render_cursor[1] + 1)
    elif freeze_static:
        state["pending"] = "fixture"
    if render_cursor and occupies_screen:
        x, y = render_cursor
        if x >= 0 and y >= 0:
            sys.stdout.write(f"\x1b[{y + 1};{x + 1}H")
    sys.stdout.flush()


def fixture_choice_group(lines: list[str]) -> dict[str, object] | None:
    """Identify a LIVE numbered-choice prompt in a rendered fixture capture.

    Claude's real AskUserQuestion layout can place descriptions, blanks, and a
    separator between option rows. The live rows are still numbered 1..N and
    exactly one row carries the selector glyph (❯ / › / >)."""
    selected_re = re.compile(r"^(?P<indent>\s*)(?P<glyph>[❯›>]) (?P<body>(?P<num>\d+)\. .*)$")
    unselected_re = re.compile(r"^(?P<indent>\s*)  (?P<body>(?P<num>\d+)\. .*)$")
    candidates: list[dict[str, object]] = []
    for idx, line in enumerate(lines):
        match = selected_re.match(line)
        selected = True
        glyph = SELECTOR_GLYPH
        if match:
            glyph = match.group("glyph")
        else:
            match = unselected_re.match(line)
            selected = False
        if not match:
            continue
        candidates.append({
            "idx": idx,
            "indent": match.group("indent"),
            "body": match.group("body"),
            "num": int(match.group("num")),
            "selected": selected,
            "glyph": glyph,
        })

    group: list[dict[str, object]] = []
    best: list[dict[str, object]] = []
    for candidate in candidates:
        if int(candidate["num"]) == 1:
            group = [candidate]
        elif group and int(candidate["num"]) == int(group[-1]["num"]) + 1:
            group.append(candidate)
        else:
            group = []
        if len(group) >= 2 and sum(1 for item in group if item["selected"]) == 1:
            best = list(group)

    if not best:
        return None
    selected_indexes = [index for index, item in enumerate(best) if item["selected"]]
    if len(selected_indexes) != 1:
        return None
    glyphs = [str(item["glyph"]) for item in best if item["selected"]]
    return {
        "idxs": [int(item["idx"]) for item in best],
        "indents": [str(item["indent"]) for item in best],
        "bodies": [str(item["body"]) for item in best],
        "selected": selected_indexes[0],
        "glyph": glyphs[0] if glyphs else SELECTOR_GLYPH,
    }


def redraw_fixture_options(rows: list[int], indents: list[str], bodies: list[str], selected: int, glyph: str, park_row: int, park_col: int) -> None:
    """Rewrite each option row in place so only `selected` carries the glyph, then
    park the cursor back where a real agent leaves it."""
    parts = []
    for i, (row, indent, body) in enumerate(zip(rows, indents, bodies)):
        marker = f"{indent}{glyph} " if i == selected else f"{indent}  "
        parts.append(f"\x1b[{row};1H\x1b[2K{marker}{body}")
    parts.append(f"\x1b[{park_row};{park_col}H")
    sys.stdout.write("".join(parts))
    sys.stdout.flush()


def handle_pending_fixture_tty(state: dict[str, str]) -> None:
    """Drive an interactive prompt-corpus fixture. Up/Down (also j/k, Ctrl-P/Ctrl-N)
    move the selector; a digit jumps to and picks that option; Enter picks the
    highlighted one; Esc — or the FIRST Ctrl-C — leaves the prompt WITHOUT exiting
    the mock program. A fixture is only a captured screen, so nothing is actually
    wired behind a choice: on resolve we say so rather than fake a follow-through."""
    rows = [int(r) for r in state.get("fixture_option_rows", "").split(",") if r]
    indents = state.get("fixture_option_indents", "").split("\x1f")
    bodies = state.get("fixture_option_bodies", "").split("\x1f")
    glyph = state.get("fixture_glyph") or SELECTOR_GLYPH
    count = min(len(rows), len(indents), len(bodies))
    if count == 0:
        time.sleep(0.25)
        return
    selected = max(0, min(count - 1, int(state.get("fixture_selected", "0"))))
    bottom_row = int(state.get("fixture_bottom_row", str(terminal_height())))
    old_settings = termios.tcgetattr(sys.stdin.fileno())
    action = "cancel"
    try:
        tty.setraw(sys.stdin.fileno())
        while True:
            key = read_key()
            if key in {"\x1b[A", "\x1bOA", "\x10", "k"}:
                selected = max(0, selected - 1)
                redraw_fixture_options(rows, indents, bodies, selected, glyph, rows[selected], len(indents[selected]) + 1)
            elif key in {"\x1b[B", "\x1bOB", "\x0e", "j"}:
                selected = min(count - 1, selected + 1)
                redraw_fixture_options(rows, indents, bodies, selected, glyph, rows[selected], len(indents[selected]) + 1)
            elif key in {"\r", "\n"}:
                action = "select"
                break
            elif key.isdigit() and 1 <= int(key) <= count:
                selected = int(key) - 1
                redraw_fixture_options(rows, indents, bodies, selected, glyph, rows[selected], len(indents[selected]) + 1)
                action = "select"
                break
            elif key in {"\x1b", "\x03"}:
                # Esc cancels. The FIRST Ctrl-C is treated exactly like Esc: it leaves
                # the fixture but does NOT raise KeyboardInterrupt, so the mock program
                # keeps running (back to its prompt) instead of dying.
                action = "cancel"
                break
    finally:
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old_settings)
        state["fixture_selected"] = str(selected)
    # Speak just beneath the frozen capture so the note scrolls in under it.
    sys.stdout.write(f"\x1b[{bottom_row};1H")
    sys.stdout.flush()
    print()
    if action == "select":
        print(f"● You picked: {bodies[selected].strip()}")
        print("● This is a mock fixture — I don't actually know the follow-through actions for that choice.")
    else:
        print("● Esc — left the prompt without picking an option.")
        print("● This is a mock fixture — I don't actually know the follow-through actions.")
    print()
    clear_pending(state)


def cmd_help() -> None:
    print("● Keyboard shortcuts")
    print("  ⎿  Enter             Submit message")
    print("     Shift+Enter       New line (also \\ + Enter)")
    print("     Up/Down           Previous prompts")
    print("     Ctrl+R            Reverse search history")
    print("     Esc               Interrupt current task")
    print("     Esc Esc           Edit previous message")
    print("     Ctrl+L            Clear screen")
    print("     Ctrl+C            Cancel prompt; press twice at composer to exit")
    print("     Ctrl+D            Exit")
    print("     Tab               Completion (slash, @file)")
    print("     ?                 Show this help")
    print()
    print("● Slash commands")
    print("  ⎿  /help             Show this help")
    print("     /clear            Clear screen and reset session")
    print("     /status           Session status")
    print("     /exit, /quit      Exit")
    print()
    print(f"● Mock-only triggers (not real {AGENT_DISPLAY_NAME})")
    print('  ⎿  <shell cmd>       Permission prompt, then REAL shell exec on Yes')
    print('     exec <cmd>        Same, for an arbitrary command')
    print('     !<cmd>            Bash mode — REAL shell exec, NO permission prompt')
    print('     sleep N           Real sleep behind a permission prompt (working state)')
    print('     yesno [N]         Mock build script — N Yes/No prompts in a row (default 3)')
    print('     mock <case>       Render a fixture; drive options (↑/↓, 1-9, Enter, Esc/Ctrl-C)')
    print('     mock list         List prompt-corpus fixture cases (also: mock, mocklist)')
    print('     mockcase, case    Aliases for mock')
    print('     ask, question     AskUserQuestion demo (arrow-key choice)')
    print('     todos             Ctrl-T style task-list overlay')
    print()


def cmd_clear(state: dict[str, str]) -> None:
    sys.stdout.write("\x1b[H\x1b[J")
    sys.stdout.flush()
    state.clear()
    print_startup()


def cmd_status(state: dict[str, str]) -> None:
    print("● Session status")
    print(f"  ⎿  Model:     {MODEL}")
    print(f"     Effort:    {EFFORT}")
    print(f"     Cwd:       {os.getcwd()}")
    print(f"     Turn:      {state.get('turn', '0')}")
    print(f"     Tokens in: {state.get('tokens_in', '0')}")
    print(f"     Tokens out:{state.get('tokens_out', '0')}")
    print()


def cmd_bang_bash(command: str) -> None:
    print_thinking(seconds=1, tokens=12)
    print(f"● Bash({command})  (bash mode)")
    result = real_exec(command)
    lines = result.split("\n") if result else [""]
    for i, line in enumerate(lines):
        prefix = "  ⎿  " if i == 0 else "     "
        print(f"{prefix}{line}")
    print()


def cmd_todos() -> None:
    print_thinking(seconds=2)
    todos = [
        ("☒", "Survey current mock fidelity"),
        ("☒", "Add thinking-verb rotation"),
        ("☐", "Wire slash commands"),
        ("☐", "Mock more tool types"),
        ("☐", "Polish conversational fallback"),
    ]
    print("● Update Todos")
    for i, (mark, text) in enumerate(todos):
        prefix = "  ⎿  " if i == 0 else "     "
        print(f"{prefix}{mark} {text}")
    print()


def print_capabilities() -> None:
    print("● Here's what I can actually do (mock — these all work for real):")
    print()
    print("  Run shell commands (real subprocess on Yes):")
    print("    date, pwd, ls, echo, whoami, hostname, uname, uptime")
    print("    cat <f>, head <f>, tail <f>, grep <pat>, find <path>")
    print("    git <...>, docker <...>, tmux <...>, ps, df, du, free")
    print("    exec <any-cmd>     ← arbitrary command, with permission prompt")
    print("    !<any-cmd>         ← bash mode, no permission prompt")
    print()
    print("  Built-in actions:")
    print("    sleep N            ← real time.sleep behind a permission prompt")
    print("    yesno [N]          ← N Yes/No permission prompts in a row (default 3)")
    print("    mock <case>        ← render a fixture and drive it (↑/↓, 1-9, Enter, Esc/Ctrl-C)")
    print("    mock list          ← list available fixture cases (mockcase/case also work)")
    print("    ask                ← AskUserQuestion demo with arrow-key nav")
    print("    todos              ← Ctrl-T style task-list overlay")
    print()
    print("  Slash commands:")
    print("    /help              ← shortcut and slash-command reference")
    print("    /status            ← session + token info")
    print("    /clear             ← clear screen")
    print("    /quit /exit        ← exit")
    print()


def fallback_response(value: str) -> None:
    snippet = value if len(value) <= 80 else value[:77] + "…"
    print_assistant(f'I don\'t know how to handle "{snippet}" — this is a mock, not real {AGENT_DISPLAY_NAME}. Here are real things I can do:')
    print()
    print_capabilities()


def handle_command(user_input: str, state: dict[str, str]) -> None:
    value = normalize_input(user_input)
    lower = value.lower()

    if handle_pending_input(value, state):
        return

    if not value:
        print()
        return

    state["turn"] = str(int(state.get("turn", "0")) + 1)
    session_increment_tokens(state, delta=random.randint(20, 80))

    if lower in {"quit", "exit", "/quit", "/exit"}:
        print_exit_message(state)
        sys.exit(0)

    if lower in {"?", "/help", "help"}:
        cmd_help()
        return
    if lower in {"/clear", "clear"}:
        cmd_clear(state)
        return
    if lower == "/status":
        cmd_status(state)
        return

    if mock_fixture_key(value) in {"mock", "mocklist", "mockcases", "fixturelist", "fixtures"}:
        print_mock_fixture_list()
        return

    m = re.match(r"^(mock|mockcase|fixture|case)\s+(.+)$", value, re.IGNORECASE)
    if m:
        alias = m.group(1).lower()
        name = m.group(2).strip()
        if mock_fixture_key(name) in {"list", "ls"}:
            print_mock_fixture_list()
        else:
            cmd_mock_fixture(state, name, freeze_static=alias != "mock")
        return

    # !<cmd> — bash mode, runs for real with NO permission prompt.
    if value.startswith("!"):
        cmd_bang_bash(value[1:].strip() or "echo ok")
        return

    # yesno / confirm / script [N] — a mock build script of N steps, each a Yes/No
    # permission prompt. Approve advances to the next; decline aborts the rest.
    m = re.match(r"^(?:yesno|confirm|script)(?:\s+(\d+))?$", lower)
    if m:
        cmd_yesno(state, int(m.group(1)) if m.group(1) else 3)
        return

    # `ask N` — N consecutive Yes/No permission asks, thinking ~1s between each (ask -> think -> repeat).
    # Bare `ask`/`question`/`choose` (no count) falls through to the single AskUserQuestion below.
    m = re.match(r"^ask\s+(\d+)$", lower)
    if m:
        cmd_yesno(state, int(m.group(1)))
        return

    # todos / plan — Ctrl-T style task list (the overlay that renders below a prompt).
    if lower in {"todos", "todo", "plan", "make a plan"}:
        cmd_todos()
        return

    # ask / question / choose — AskUserQuestion-style choice prompt (arrow-key nav).
    if len(value) <= 60 and re.search(r"\b(question|ask|choose)\b", lower):
        print_thinking()
        question = "Who is the greatest tennis player of all time?"
        options = ["Novak Djokovic", "Roger Federer", "Rafael Nadal"]
        print_question(question, options)
        set_pending_question(state, question, options)
        return

    # sleep / wait N — real time.sleep behind a permission prompt (drives the working state).
    m = re.match(r"^(sleep|wait)\s+(\d+(?:\.\d+)?)", value, re.IGNORECASE)
    if m:
        secs = m.group(2)
        unit = "second" if float(secs) == 1 else "seconds"
        description = f"Sleep for {secs} {unit}"
        n = print_bash_prompt(value, description)
        set_pending_permission(state, value, description, lines=n)
        return

    # exec / execute <cmd> — arbitrary command via a real shell, behind a permission prompt.
    m = re.match(r"^(exec|execute)\s+(.+)$", value, re.IGNORECASE)
    if m:
        command = m.group(2).strip() or "echo ok"
        description = "Execute requested command (real shell)"
        n = print_bash_prompt(command, description)
        set_pending_permission(state, command, description, lines=n)
        state["real_exec"] = "1"
        return

    # A recognized shell command — permission prompt, runs for real on Yes.
    if looks_like_shell_command(value):
        description = describe_shell_command(value)
        n = print_bash_prompt(value, description)
        set_pending_permission(state, value, description, lines=n)
        state["real_exec"] = "1"
        return

    print_thinking()
    fallback_response(value)


def setup_history() -> None:
    os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
    if os.path.exists(HISTORY_FILE):
        readline.read_history_file(HISTORY_FILE)
    readline.set_history_length(HISTORY_LIMIT)
    atexit.register(lambda: readline.write_history_file(HISTORY_FILE))


def main() -> None:
    setup_history()
    print_startup()
    state: dict[str, str] = {}
    while True:
        try:
            if state.get("pending") == "permission" and sys.stdin.isatty():
                handle_pending_permission_tty(state)
                continue
            if state.get("pending") == "question" and sys.stdin.isatty():
                handle_pending_question_tty(state)
                continue
            if state.get("pending") == "fixture":
                if state.get("fixture_interactive") == "1" and sys.stdin.isatty():
                    handle_pending_fixture_tty(state)
                else:
                    time.sleep(0.25)
                continue
            pending = state.get("pending") == "permission"
            prompt = "" if pending else f"{PROMPT_GLYPH} "
            if sys.stdin.isatty() and not pending:
                user_input = read_live_composer(state)
                if not pending and user_input.strip() and PERMISSION_STYLE != "codex":
                    print_user_header(user_input)
            else:
                user_input = input(prompt)
                print()
            clear_ctrl_c_exit_window(state)
            handle_command(user_input, state)
        except KeyboardInterrupt:
            # Match the REAL agents exactly: Claude shows this hint on the first
            # Ctrl-C and exits on the second; Codex has no hint — a single idle
            # Ctrl-C exits straight away.
            if AGENT_NAME == "claude" and sys.stdin.isatty() and not ctrl_c_requests_exit(state):
                # The composer status line shows "Press Ctrl-C again to exit" while armed
                # (see live_composer_status_line); a second Ctrl-C falls through and exits.
                continue
            print()
            if AGENT_NAME == "codex":
                print_codex_exit_footer(state)
            sys.exit(0)
        except EOFError:
            print()
            if AGENT_NAME == "codex":
                print_codex_exit_footer(state)
            sys.exit(0)


if __name__ == "__main__":
    main()
