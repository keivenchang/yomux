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
    "  ⏵⏵ accept edits on (shift+tab to cycle) · ← for agents",
    "  ⏸ plan mode on (shift+tab to cycle) · ← for agents",
    "  ⏵⏵ auto mode on (shift+tab to cycle) · ← for agents",
]
CLAUDE_DEFAULT_STATUS_LINE = "  ? for shortcuts · ← for agents"
CLAUDE_SHORTCUT_LINES = [
    "  ! for shell mode        double tap esc to clear input      ctrl + shift + _ to undo",
    "  / for commands          shift + tab to auto-accept edits   ctrl + z to suspend",
    "  @ for file paths        ctrl + o for verbose output        ctrl + v to paste images",
    "  /btw for side question  ctrl + t to toggle tasks           alt + p to switch model",
    "                          \\⏎ for newline                     ctrl + s to stash prompt",
    "                                                             ctrl + g to edit in $EDITOR",
    "                                                             /keybindings to customize",
]
CODEX_QUEUE_HINT = "tab to queue message"
CODEX_CONTEXT_LEFT = "56% context left"
CODEX_QUEUED_EDIT_HINT = "shift + ← edit last queued message"
PROMPT_CORPUS_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "prompt_corpus"
MOCK_FIXTURE_CASES: list[dict[str, object]] | None = None
LAST_PERMISSION_RENDER_REGION: tuple[int, int] | None = None
DISPLAY_CWD_OVERRIDE = ""


def transcript_bullet() -> str:
    return "•" if PERMISSION_STYLE == "codex" else "●"


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
    display_cwd_override: str = "",
) -> None:
    global AGENT_NAME, AGENT_DISPLAY_NAME, AGENT_PRODUCT_NAME, HISTORY_FILE
    global VERSION, MODEL, EFFORT, MODEL_LINE, PROMPT_GLYPH, SELECTOR_GLYPH, PERMISSION_STYLE
    global STARTUP_STYLE, CODEX_BYPASS_HOOK_TRUST, CODEX_DANGER_FULL_ACCESS, DISPLAY_CWD_OVERRIDE

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
    DISPLAY_CWD_OVERRIDE = display_cwd_override


def configure_claude_mock(*, display_cwd_override: str = "") -> None:
    configure(
        agent_name="claude",
        agent_display_name="Claude",
        agent_product_name="Claude Code",
        history_file="~/.cache/yolomux/mock_claude_history",
        version="2.1.185",
        model="Opus 4.8 (1M context)",
        effort="xhigh",
        model_line="Opus 4.8 (1M context) with xhigh effort · API Usage Billing",
        prompt_glyph="❯",
        selector_glyph="❯",
        permission_style="claude",
        display_cwd_override=display_cwd_override,
    )


def configure_codex_mock(
    *,
    display_cwd_override: str = "",
    codex_bypass_hook_trust: bool = False,
    codex_danger_full_access: bool = False,
    model: str = "gpt-5.5",
    effort: str = "xhigh",
) -> None:
    configure(
        agent_name="codex",
        agent_display_name="Codex",
        agent_product_name="OpenAI Codex",
        history_file="~/.cache/yolomux/mock_codex_history",
        version="0.141.0",
        model=model,
        effort=effort,
        model_line=f"{model} {effort} · API Usage Billing",
        prompt_glyph="›",
        selector_glyph="›",
        permission_style="codex",
        startup_style="codex",
        codex_bypass_hook_trust=codex_bypass_hook_trust,
        codex_danger_full_access=codex_danger_full_access,
        display_cwd_override=display_cwd_override,
    )


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
    cwd = DISPLAY_CWD_OVERRIDE or os.getcwd()
    home = os.path.expanduser("~")
    if cwd == home:
        return "~"
    if cwd.startswith(home + "/"):
        return "~" + cwd[len(home):]
    return cwd


def print_startup(state: dict[str, str] | None = None) -> None:
    if STARTUP_STYLE == "codex":
        print_codex_startup(state)
        return
    width = terminal_width()
    if sys.stdout.isatty():
        sys.stdout.write("\x1b[H\x1b[J")
        sys.stdout.flush()
        if terminal_height() < 8:
            return
    print_minimal_header()
    if not sys.stdout.isatty():
        print()
        print_prompt_box(f"{PROMPT_GLYPH} {live_composer_suggestion()}", width)
        print()


def print_minimal_header() -> None:
    print(f" {CLAUDE_ORANGE}▐▛███▜▌{ANSI_RESET}   {AGENT_PRODUCT_NAME} v{VERSION}")
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
CODEX_CAPTURED_STATUS_RE = re.compile(
    r"^\s*(?:gpt|o\d|codex)[A-Za-z0-9_.-]*\s+\S+(?:\s+\S+)?\s+·\s+\S.*$",
    re.IGNORECASE,
)
CODEX_CAPTURED_PROMPT_RE = re.compile(r"^\s*[›>]\s+(?!\d+[.:]\s+)\S.*$")
CLAUDE_CAPTURED_WORKING_RE = re.compile(
    r"^(?P<marker>\S)\s+(?P<verb>[^…]+)…\s+\((?P<elapsed>[^)]*?)\s+·\s+↓\s+(?P<tokens>\d+)\s+tokens\)",
    re.IGNORECASE,
)
# Enterprise identity lines shown under the robot in the real welcome box.
WELCOME_ORG_LINE = "· Acme Corp - Power Users"
WELCOME_PLAN_LABEL = "Claude Enterprise"


def plain_capture_line(text: str) -> str:
    return ANSI_RE.sub("", str(text or "")).replace("\xa0", " ").rstrip()


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


def print_codex_startup(state: dict[str, str] | None = None) -> None:
    inner = 47

    def box_line(text: str = "") -> str:
        return "│" + clipped(text, inner) + "│"

    box_line_count = 6 + (1 if CODEX_DANGER_FULL_ACCESS else 0)
    footer_line_count = len(codex_composer_footer_lines())
    launch_context_line_count = 1
    include_box = True
    include_tip = True
    include_warnings = CODEX_BYPASS_HOOK_TRUST
    if state is not None:
        state.pop("codex_clear_startup_on_first_submit", None)
    if sys.stdout.isatty():
        # The wrapper owns a fixed footer even when launched from a shell prompt.
        # Short-pane budgeting must leave room for both that footer and the command
        # line already visible above the startup chrome; otherwise the footer lands
        # on the box's bottom border.
        height = terminal_height()
        available_startup_rows = max(0, height - footer_line_count - launch_context_line_count)
        include_box = available_startup_rows >= box_line_count + 1
        include_tip = available_startup_rows >= box_line_count + 3
        warning_line_count = 6 if CODEX_BYPASS_HOOK_TRUST else 0
        include_warnings = (
            CODEX_BYPASS_HOOK_TRUST
            and available_startup_rows >= warning_line_count + box_line_count + 3
        )
        if state is not None and include_box and not include_tip:
            state["codex_clear_startup_on_first_submit"] = "1"
        reset_terminal_scroll_region(preserve_cursor=True)

    if include_warnings:
        print("⚠ `--dangerously-bypass-hook-trust` is enabled. Enabled hooks may run without review for this")
        print("  invocation.")
        print()
    if include_box:
        print("╭" + ("─" * inner) + "╮")
        print(box_line(f" >_ {AGENT_PRODUCT_NAME} (v{VERSION})"))
        print(box_line())
        print(box_line(f" model:       {MODEL} {EFFORT}   /model to change"))
        print(box_line(f" directory:   {display_cwd()}"))
        if CODEX_DANGER_FULL_ACCESS:
            print(box_line(" permissions: YOLO mode"))
        print("╰" + ("─" * inner) + "╯")
    if include_tip:
        print()
        print("  Tip: New Use /fast to enable our fastest inference with increased plan usage.")
        print()
    if include_warnings:
        print("⚠ `--dangerously-bypass-hook-trust` is enabled. Enabled hooks may run without review for this")
        print("  invocation.")
        print()
    if not sys.stdout.isatty():
        print()
        print(f"{PROMPT_GLYPH} {live_composer_suggestion()}")
        print()
        print(f"  {MODEL} {EFFORT} · {display_cwd()}")
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
    total = max(0, int(seconds))
    minutes, remaining = divmod(total, 60)
    if minutes:
        return f"{minutes}m {remaining}s"
    return f"{remaining}s"


def codex_working_line(seconds: float) -> str:
    return f"• Working ({format_working_elapsed(seconds)} • esc to interrupt)"


def codex_background_status_suffix(background: bool = False) -> str:
    if not background:
        return ""
    return " · 1 background terminal running · /ps to view · /stop to close"


def codex_working_display_line(seconds: float, background: bool = False) -> str:
    suffix = f" ({format_working_elapsed(seconds)} • esc to interrupt){codex_background_status_suffix(background)}"
    return f"{ANSI_DIM}• {ANSI_RESET}{ANSI_BOLD}Working{ANSI_RESET}{ANSI_DIM}{suffix}{ANSI_RESET}"


def codex_queued_followup_lines(messages: list[str], width: int | None = None) -> list[str]:
    if not messages:
        return []
    width = terminal_width() if width is None else width
    body_width = max(12, width - 4)
    lines = ["• Queued follow-up inputs"]
    for message in messages:
        cleaned = " ".join(message.splitlines())
        wrapped = textwrap.wrap(cleaned, width=body_width) or [""]
        lines.append(f"{ANSI_DIM}{ANSI_ITALIC}  ↳ {wrapped[0]}{ANSI_RESET}")
        for continuation in wrapped[1:]:
            lines.append(f"{ANSI_DIM}{ANSI_ITALIC}    {continuation}{ANSI_RESET}")
    lines.append(f"{ANSI_DIM}{CODEX_QUEUED_EDIT_HINT}{ANSI_RESET}")
    return lines


def codex_working_block_lines(
    seconds: float,
    text: str = "",
    cursor: int = 0,
    background: bool = False,
    queued_messages: list[str] | None = None,
) -> list[str]:
    queued_messages = queued_messages or []
    prompt_display, status_display, _cursor_col = composer_render_parts(
        text,
        cursor,
        state={"codex_working": "1"} if text and not queued_messages else None,
    )
    lines = [
        "",
        codex_working_line(seconds) + codex_background_status_suffix(background),
        "",
    ]
    if queued_messages:
        lines.extend(codex_queued_followup_lines(queued_messages))
    else:
        lines.append("")
    lines.extend([
        prompt_display,
        "",
        status_display,
    ])
    return lines


def codex_working_block_start_row(line_count: int = 7) -> int:
    return max(1, terminal_height() - line_count + 1)


def write_codex_working_block(
    seconds: float,
    text: str = "",
    cursor: int = 0,
    background: bool = False,
    queued_messages: list[str] | None = None,
) -> tuple[int, int]:
    queued_messages = queued_messages or []
    lines = codex_working_block_lines(seconds, text, cursor, background, queued_messages)
    start_row = codex_working_block_start_row(len(lines))
    prompt_index = max(0, len(lines) - 3)
    for index, line in enumerate(lines):
        row = start_row + index
        if row > terminal_height():
            continue
        display = codex_working_display_line(seconds, background) if index == 1 else line
        sys.stdout.write(f"\x1b[{row};1H\x1b[2K{display}")
    _prompt_display, _status_display, cursor_col = composer_render_parts(
        text,
        cursor,
        state={"codex_working": "1"} if text and not queued_messages else None,
    )
    prompt_row = min(terminal_height(), start_row + prompt_index)
    sys.stdout.write(f"\x1b[{prompt_row};{cursor_col}H")
    bottom = max(1, start_row - 1)
    sys.stdout.write(f"\x1b7\x1b[1;{bottom}r\x1b8")
    sys.stdout.flush()
    return start_row, len(lines)


def clear_codex_working_block(start_row: int | None = None, line_count: int = 7, restore_footer: bool = True) -> None:
    if start_row is None:
        start_row = codex_working_block_start_row(line_count)
    for index in range(max(1, line_count)):
        row = start_row + index
        if row > terminal_height():
            continue
        sys.stdout.write(f"\x1b[{row};1H\x1b[2K")
    sys.stdout.write(f"\x1b[{start_row};1H")
    if restore_footer:
        render_live_composer("", 0)
        reserve_output_region_above_live_composer()
    sys.stdout.flush()


def drain_codex_queued_input(text: str, cursor: int, queued_messages: list[str]) -> tuple[str, int, list[str]]:
    if not sys.stdin.isatty():
        return text, cursor, queued_messages
    try:
        ready, _write, _error = select.select([sys.stdin], [], [], 0)
    except (OSError, ValueError):
        return text, cursor, queued_messages
    if not ready:
        return text, cursor, queued_messages
    try:
        raw = os.read(sys.stdin.fileno(), 1024).decode(errors="ignore")
    except OSError:
        return text, cursor, queued_messages
    for key in raw:
        if key in {"\x7f", "\b"}:
            if cursor > 0:
                text = text[:cursor - 1] + text[cursor:]
                cursor -= 1
        elif key == "\t":
            if text.strip():
                queued_messages.append(text)
                text = ""
                cursor = 0
        elif key == "\x15":
            text = ""
            cursor = 0
        elif key == "\x01":
            cursor = 0
        elif key == "\x05":
            cursor = len(text)
        elif key >= " ":
            text = text[:cursor] + key + text[cursor:]
            cursor += 1
    return text, cursor, queued_messages


def apply_codex_working_key(text: str, cursor: int, key: str) -> tuple[str, int, str]:
    if key in {"\x1b", "\x03"}:
        return text, cursor, "interrupt"
    if key == "\t":
        return text, cursor, "queue" if text.strip() else ""
    if key in {"\x7f", "\b"}:
        if cursor > 0:
            text = text[:cursor - 1] + text[cursor:]
            cursor -= 1
        return text, cursor, ""
    if key in {"\x1b[D", "\x1bOD", "\x02"}:
        return text, max(0, cursor - 1), ""
    if key in {"\x1b[C", "\x1bOC", "\x06"}:
        return text, min(len(text), cursor + 1), ""
    if key == "\x01":
        return text, 0, ""
    if key == "\x05":
        return text, len(text), ""
    if key == "\x0b":
        return text[:cursor], min(cursor, len(text[:cursor])), ""
    if key == "\x15":
        return text[cursor:], 0, ""
    if key == "\x17":
        start = cursor
        while start > 0 and text[start - 1].isspace():
            start -= 1
        while start > 0 and not text[start - 1].isspace():
            start -= 1
        return text[:start] + text[cursor:], start, ""
    if len(key) == 1 and key >= " ":
        text = text[:cursor] + key + text[cursor:]
        cursor += len(key)
    return text, cursor, ""


def print_codex_working(seconds: float, background: bool = False) -> str:
    if not sys.stdout.isatty():
        print(codex_working_line(seconds) + codex_background_status_suffix(background))
        return ""

    tick = 0.12
    total_ticks = max(1, int(seconds / tick))
    start_row = None
    line_count = 7
    queued_text = ""
    queued_cursor = 0
    queued_messages: list[str] = []
    old_settings = None
    if sys.stdin.isatty():
        old_settings = termios.tcgetattr(sys.stdin.fileno())
        tty.setraw(sys.stdin.fileno())
    try:
        for i in range(total_ticks + 1):
            queued_text, queued_cursor, queued_messages = drain_codex_queued_input(queued_text, queued_cursor, queued_messages)
            elapsed = min(seconds, i * tick)
            start_row, line_count = write_codex_working_block(
                elapsed,
                queued_text,
                queued_cursor,
                background,
                queued_messages,
            )
            if i < total_ticks:
                time.sleep(tick)
    finally:
        if old_settings is not None:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old_settings)
        clear_codex_working_block(start_row, line_count)
    return queued_text


def mock_fixture_is_codex_goal_active(case: dict[str, object]) -> bool:
    if PERMISSION_STYLE != "codex":
        return False
    keys = case.get("keys")
    return isinstance(keys, set) and "goal_active" in keys


def mock_fixture_is_codex_live_working(case: dict[str, object]) -> bool:
    if PERMISSION_STYLE != "codex" or str(case.get("agent") or "") != "codex":
        return False
    expected = case.get("expected") if isinstance(case.get("expected"), dict) else {}
    screen_key = str(expected.get("screen_key") or "").strip()
    reason_code = str(expected.get("reason_code") or "").strip()
    return screen_key == "working" or reason_code == "busy"


def mock_fixture_is_codex_draft_only(case: dict[str, object]) -> bool:
    if PERMISSION_STYLE != "codex" or str(case.get("agent") or "") != "codex":
        return False
    expected = case.get("expected") if isinstance(case.get("expected"), dict) else {}
    screen_key = str(expected.get("screen_key") or "").strip()
    composer_key = str(expected.get("composer_key") or "").strip()
    return screen_key == "input-draft" or (
        composer_key == "draft"
        and expected.get("ask") is not True
        and expected.get("approval_visible") is not True
        and screen_key not in {"approval", "needs-input"}
    )


def mock_fixture_is_claude_live_working(case: dict[str, object]) -> bool:
    if PERMISSION_STYLE == "codex" or str(case.get("agent") or "") != "claude":
        return False
    expected = case.get("expected") if isinstance(case.get("expected"), dict) else {}
    screen_key = str(expected.get("screen_key") or "").strip()
    reason_code = str(expected.get("reason_code") or "").strip()
    return screen_key == "working" or reason_code == "busy"


def codex_fixture_draft_text(case: dict[str, object]) -> str:
    capture = str(case.get("styled_capture") or case.get("raw_capture") or "")
    for line in reversed(capture.splitlines()):
        plain = plain_capture_line(line).strip()
        if not CODEX_CAPTURED_PROMPT_RE.match(plain):
            continue
        text = re.sub(r"^\s*[›>]\s+", "", plain).strip()
        if text and text != live_composer_suggestion():
            return text
    return ""


def start_codex_draft_mock(state: dict[str, str], case: dict[str, object]) -> None:
    draft = codex_fixture_draft_text(case)
    state["fixture_case"] = str(case.get("case_name") or "typed_draft")
    if draft:
        state["composer_prefill"] = draft
    if sys.stdout.isatty():
        render_live_composer(draft, len(draft), state=state)
        reserve_output_region_above_live_composer(state)


def parse_working_elapsed_seconds(value: str) -> int:
    total = 0.0
    for amount, unit in re.findall(r"(\d+(?:\.\d+)?)\s*([hms])", value, flags=re.IGNORECASE):
        factor = {"h": 3600, "m": 60, "s": 1}[unit.lower()]
        total += float(amount) * factor
    return max(0, int(total))


CODEX_WORKING_ELAPSED_RE = re.compile(r"[•◦]\s+Working\s+\((?P<elapsed>[^()]*?)\s+•\s+esc to interrupt\)", re.IGNORECASE)


def codex_live_working_base_seconds(case: dict[str, object]) -> int:
    if mock_fixture_is_codex_goal_active(case):
        return 190
    capture = str(case.get("styled_capture") or case.get("raw_capture") or "")
    match = CODEX_WORKING_ELAPSED_RE.search(plain_capture_line(capture))
    if not match:
        return 1
    return parse_working_elapsed_seconds(match.group("elapsed"))


def start_codex_live_working_mock(state: dict[str, str], case: dict[str, object]) -> None:
    state["pending"] = "codex-working"
    state["fixture_case"] = str(case.get("case_name") or "working")
    state["codex_working_started_at"] = f"{time.time():.6f}"
    state["codex_working_base_seconds"] = str(codex_live_working_base_seconds(case))
    state["codex_working_text"] = ""
    state["codex_working_cursor"] = "0"
    state["codex_working_queued"] = ""
    if sys.stdout.isatty():
        write_codex_working_block(float(state["codex_working_base_seconds"]))
        sys.stdout.flush()


def start_codex_goal_active_mock(state: dict[str, str], case: dict[str, object]) -> None:
    start_codex_live_working_mock(state, case)
    state["pending"] = "codex-goal-active"


def claude_fixture_working_fields(case: dict[str, object]) -> dict[str, str]:
    capture = str(case.get("styled_capture") or case.get("raw_capture") or "")
    fields = {
        "marker": "·",
        "verb": "Clauding",
        "base_seconds": "1",
        "base_tokens": "1",
        "footer_status": "  ⏸ plan mode on (shift+tab to cycle) · esc to interrupt",
    }
    for line in capture.splitlines():
        match = CLAUDE_CAPTURED_WORKING_RE.match(plain_capture_line(line).strip())
        if not match:
            continue
        fields["marker"] = match.group("marker")
        fields["verb"] = match.group("verb").strip() or fields["verb"]
        fields["base_seconds"] = str(parse_working_elapsed_seconds(match.group("elapsed")))
        fields["base_tokens"] = match.group("tokens")
    for line in reversed(capture.splitlines()):
        plain = plain_capture_line(line).strip()
        if "shift+tab to cycle" in plain and "esc to interrupt" in plain:
            fields["footer_status"] = plain_capture_line(line)
            break
    return fields


def strip_claude_captured_working_footer(lines: list[str]) -> list[str]:
    if PERMISSION_STYLE == "codex":
        return lines
    for index in range(len(lines) - 1, -1, -1):
        if CLAUDE_CAPTURED_WORKING_RE.match(plain_capture_line(lines[index]).strip()):
            stripped = lines[:index]
            while stripped and not plain_capture_line(stripped[-1]).strip():
                stripped.pop()
            return stripped
    return lines


def claude_working_line(seconds: float, state: dict[str, str]) -> str:
    base_tokens = int(state.get("claude_working_base_tokens", "1") or 1)
    base_seconds = float(state.get("claude_working_base_seconds", "1") or 1)
    elapsed = max(0.0, seconds)
    token_delta = max(0, int((elapsed - base_seconds) * 24))
    marker = state.get("claude_working_marker", "·") or "·"
    verb = state.get("claude_working_verb", "Clauding") or "Clauding"
    return f"{marker} {verb}… ({format_working_elapsed(elapsed)} · ↓ {base_tokens + token_delta} tokens)"


def render_lines_above_row(lines: list[str], bottom_row: int) -> None:
    bottom_row = max(1, bottom_row)
    for row in range(1, bottom_row + 1):
        sys.stdout.write(f"\x1b[{row};1H\x1b[2K")
    if not lines:
        return
    visible_lines = lines[-bottom_row:]
    start_row = max(1, bottom_row - len(visible_lines) + 1)
    for offset, line in enumerate(visible_lines):
        row = start_row + offset
        if row <= bottom_row:
            sys.stdout.write(f"\x1b[{row};1H{line}")


def write_claude_working_block(seconds: float, state: dict[str, str]) -> int:
    state["claude_working"] = "1"
    text = state.get("claude_working_text", "")
    cursor = max(0, min(len(text), int(state.get("claude_working_cursor", "0") or 0)))
    render_live_composer(text, cursor, state=state)
    working_row = max(1, live_composer_footer_top(text, False, state) - 1)
    line = clipped(claude_working_line(seconds, state), terminal_width())
    sys.stdout.write(f"\x1b7\x1b[{working_row};1H\x1b[2K{line}\x1b8")
    bottom = max(1, working_row - 1)
    sys.stdout.write(f"\x1b7\x1b[1;{bottom}r\x1b8")
    sys.stdout.flush()
    return working_row


def clear_claude_working_block(state: dict[str, str], working_row: int | None = None) -> None:
    if working_row is None:
        working_row = max(1, live_composer_footer_top(state=state) - 1)
    if 1 <= working_row <= terminal_height():
        sys.stdout.write(f"\x1b[{working_row};1H\x1b[2K")
    state.pop("claude_working", None)
    render_live_composer(state.get("composer_prefill", ""), len(state.get("composer_prefill", "")), state=state)
    reserve_output_region_above_live_composer(state)
    sys.stdout.flush()


def apply_claude_working_key(text: str, cursor: int, key: str) -> tuple[str, int, str]:
    if key in {"\x1b", "\x03"}:
        return text, cursor, "interrupt"
    if key in {"\x7f", "\b"}:
        if cursor > 0:
            text = text[:cursor - 1] + text[cursor:]
            cursor -= 1
        return text, cursor, ""
    if key in {"\x1b[D", "\x1bOD", "\x02"}:
        return text, max(0, cursor - 1), ""
    if key in {"\x1b[C", "\x1bOC", "\x06"}:
        return text, min(len(text), cursor + 1), ""
    if key == "\x01":
        return text, 0, ""
    if key == "\x05":
        return text, len(text), ""
    if key == "\x15":
        return "", 0, ""
    if len(key) == 1 and key >= " ":
        text = text[:cursor] + key + text[cursor:]
        cursor += len(key)
    return text, cursor, ""


def start_claude_live_working_mock(state: dict[str, str], case: dict[str, object], lines: list[str] | None = None) -> None:
    fields = claude_fixture_working_fields(case)
    state["pending"] = "claude-working"
    state["fixture_case"] = str(case.get("case_name") or "working")
    state["claude_working_started_at"] = f"{time.time():.6f}"
    state["claude_working_base_seconds"] = fields["base_seconds"]
    state["claude_working_base_tokens"] = fields["base_tokens"]
    state["claude_working_marker"] = fields["marker"]
    state["claude_working_verb"] = fields["verb"]
    state["claude_working_footer_status"] = fields["footer_status"]
    state["claude_working_text"] = ""
    state["claude_working_cursor"] = "0"
    if sys.stdout.isatty():
        source_lines = lines if lines is not None else str(case.get("styled_capture") or "").splitlines()
        clipped_source_lines = [clip_display_width(line, terminal_width()) for line in source_lines]
        body_lines = strip_claude_captured_working_footer(clipped_source_lines)
        footer_top = live_composer_footer_top("", False, {**state, "claude_working": "1"})
        render_lines_above_row(body_lines, max(1, footer_top - 2))
        write_claude_working_block(float(state["claude_working_base_seconds"]), state)


def handle_claude_live_working_tty(state: dict[str, str]) -> None:
    old_settings = termios.tcgetattr(sys.stdin.fileno())
    working_row = None
    try:
        tty.setraw(sys.stdin.fileno())
        while state.get("pending") == "claude-working":
            started_at = float(state.get("claude_working_started_at", str(time.time())) or time.time())
            base_seconds = float(state.get("claude_working_base_seconds", "1") or 1)
            elapsed = base_seconds + max(0, time.time() - started_at)
            working_row = write_claude_working_block(elapsed, state)
            ready, _write, _error = select.select([sys.stdin.fileno()], [], [], 0.12)
            if not ready:
                continue
            key = read_key()
            text = state.get("claude_working_text", "")
            cursor = max(0, min(len(text), int(state.get("claude_working_cursor", "0") or 0)))
            text, cursor, action = apply_claude_working_key(text, cursor, key)
            state["claude_working_text"] = text
            state["claude_working_cursor"] = str(cursor)
            if action == "interrupt":
                clear_pending(state)
                if text:
                    state["composer_prefill"] = text
                break
    finally:
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old_settings)
        clear_claude_working_block(state, working_row)


def handle_codex_live_working_tty(state: dict[str, str]) -> None:
    old_settings = termios.tcgetattr(sys.stdin.fileno())
    start_row = None
    line_count = 7
    try:
        tty.setraw(sys.stdin.fileno())
        while state.get("pending") in {"codex-working", "codex-goal-active"}:
            started_at = float(state.get("codex_working_started_at", state.get("codex_goal_active_started_at", str(time.time()))) or time.time())
            base_seconds = float(state.get("codex_working_base_seconds", state.get("codex_goal_active_base_seconds", "1")) or 1)
            text = state.get("codex_working_text", state.get("codex_goal_active_text", ""))
            cursor = max(0, min(len(text), int(state.get("codex_working_cursor", state.get("codex_goal_active_cursor", "0")) or 0)))
            queued_raw = state.get("codex_working_queued", state.get("codex_goal_active_queued", ""))
            queued_messages = [message for message in queued_raw.split("\n") if message]
            elapsed = base_seconds + max(0, time.time() - started_at)
            start_row, line_count = write_codex_working_block(elapsed, text, cursor, queued_messages=queued_messages)
            ready, _write, _error = select.select([sys.stdin.fileno()], [], [], 0.12)
            if not ready:
                continue
            key = read_key()
            text, cursor, action = apply_codex_working_key(text, cursor, key)
            if action == "queue":
                if text.strip():
                    queued_messages.append(text)
                text = ""
                cursor = 0
            state["codex_working_text"] = text
            state["codex_working_cursor"] = str(cursor)
            state["codex_working_queued"] = "\n".join(queued_messages)
            if action == "interrupt":
                clear_pending(state)
                if text:
                    state["composer_prefill"] = text
                break
    finally:
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old_settings)
        clear_codex_working_block(start_row, line_count)


def handle_codex_goal_active_tty(state: dict[str, str]) -> None:
    handle_codex_live_working_tty(state)


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
        if PERMISSION_STYLE == "codex":
            write_codex_working_block(max(1, time.time() - started_at), background=True)
        else:
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
    if PERMISSION_STYLE == "codex":
        clear_codex_working_block()
    else:
        finish_working_status_block(agent_working_status_lines(0, started_at, verb, tip))
    return result, elapsed


def print_assistant(text: str) -> None:
    """Assistant turn: bullet once on first line, wrapped continuations indented under."""
    width = max(40, terminal_width() - 4)
    paragraphs = text.split("\n\n")
    bullet_used = False
    for pi, para in enumerate(paragraphs):
        if pi > 0:
            print()
        for line in textwrap.wrap(para, width=width) or [""]:
            if not bullet_used:
                print(f"{transcript_bullet()} {line}")
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
    print(f"{transcript_bullet()} {question}")
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
        print(f"{transcript_bullet()} Cancelled.")
    else:
        print(f"{transcript_bullet()} You picked: {options[chosen]}")
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


def codex_composer_footer_lines(
    text: str = "",
    cursor: int = 0,
    armed_exit: bool = False,
    state: dict[str, str] | None = None,
) -> list[str]:
    prompt_display, status_display, _cursor_col = composer_render_parts(text, cursor, armed_exit, state)
    return ["", prompt_display, "", status_display]


def codex_permission_prompt_lines(command: str, selected: int, compact: bool = False) -> list[str]:
    cmd_lines = textwrap.wrap(command, width=76) or [""]
    choices = permission_choice_lines(selected, command)
    while choices and not choices[-1]:
        choices.pop()
    if compact:
        choices = [line for line in choices if line]
    if compact:
        lines = [
            "  Would you like to run the following command?",
            "",
        ]
        for index, line in enumerate(cmd_lines):
            lines.append(f"  {'$ ' if index == 0 else '  '}{line}")
        lines.append("")
        lines.extend(choices)
        return lines
    lines = [
        f"◦ {preview_verb(command)} {command}",
        "",
        "",
        "  Would you like to run the following command?",
        "",
    ]
    for index, line in enumerate(cmd_lines):
        lines.append(f"  {'$ ' if index == 0 else '  '}{line}")
    lines.append("")
    lines.extend(choices)
    return lines


def render_codex_permission_prompt(command: str, selected: int = 0) -> tuple[int, int]:
    lines = codex_permission_prompt_lines(command, selected)
    footer_lines = codex_composer_footer_lines()
    height = terminal_height()
    body_bottom = max(1, height - len(footer_lines))
    if len(lines) > body_bottom:
        lines = codex_permission_prompt_lines(command, selected, compact=True)
    visible_lines = lines[-body_bottom:]
    start_row = max(1, body_bottom - len(visible_lines) + 1)
    for row in range(start_row, height + 1):
        sys.stdout.write(f"\x1b[{row};1H\x1b[2K")
    for offset, line in enumerate(visible_lines):
        row = start_row + offset
        if row <= body_bottom:
            sys.stdout.write(f"\x1b[{row};1H{line}")
    footer_start = max(1, height - len(footer_lines) + 1)
    for offset, line in enumerate(footer_lines):
        row = footer_start + offset
        if row <= height:
            sys.stdout.write(f"\x1b[{row};1H\x1b[2K{line}")
    selected_row = next(
        (start_row + index for index, line in enumerate(visible_lines) if line.startswith(SELECTOR_GLYPH)),
        body_bottom,
    )
    sys.stdout.write(f"\x1b[{min(height, selected_row)};1H")
    sys.stdout.flush()
    return start_row, height


def print_bash_prompt(command: str, description: str = "Run shell command") -> int:
    """Render the Bash permission block. Returns total newlines emitted so
    the approval/cancel handler can erase exactly this block."""
    global LAST_PERMISSION_RENDER_REGION
    LAST_PERMISSION_RENDER_REGION = None
    # A command awaiting approval is NOT running yet: real Claude shows ONLY the
    # permission block here. The `● Bash(...)`/Running/result render happens AFTER
    # approval (see approve_pending_permission). Emitting no `⎿ Running…` working
    # line is also what lets the auto-approve detector see a clean LIVE prompt
    # instead of mistaking the screen for "agent working" and skipping it.
    cmd_lines = textwrap.wrap(command, width=76) or [""]
    if PERMISSION_STYLE == "codex" and sys.stdout.isatty():
        LAST_PERMISSION_RENDER_REGION = render_codex_permission_prompt(command, 0)
        top, bottom = LAST_PERMISSION_RENDER_REGION
        return bottom - top + 1
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
    """Print the agent's done marker after a tool finishes."""
    print(f"{transcript_bullet()} Done.")
    print()
    print(f"* {random.choice(PAST_VERBS)} for {max(1, seconds)}s")
    print()


def print_tool_result(command: str, result: str = "ok") -> None:
    print(f"{transcript_bullet()} Bash({command})")
    lines = result.split("\n") if result else [""]
    for i, line in enumerate(lines):
        prefix = "  ⎿  " if i == 0 else "     "
        print(f"{prefix}{line}")
    print()
    print_done_summary(seconds=random.randint(2, 9))


def print_tool_error(command: str) -> None:
    print(f"{transcript_bullet()} Bash({command})")
    print("  ⎿  Error: Exit code 1")
    print(f"     bwrap: Can't create file at {os.path.expanduser('~')}/.{AGENT_NAME}/skills: Is a directory")
    print()


def print_tool_multiline(command: str, lines: list[str], more: int = 0) -> None:
    print(f"{transcript_bullet()} Bash({command})")
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
    if LAST_PERMISSION_RENDER_REGION is not None:
        state["prompt_top_row"] = str(LAST_PERMISSION_RENDER_REGION[0])
        state["prompt_bottom_row"] = str(LAST_PERMISSION_RENDER_REGION[1])


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
    print(f"{transcript_bullet()} Mock build script — {count} step{plural}, each needs Yes/No.")
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
        print(f"{transcript_bullet()} Build script aborted at step {idx}/{total} ({skipped} step{plural} skipped).")
        print()
        clear_yesno(state)
        return
    if not remaining:
        print(f"{transcript_bullet()} Build script complete — {total}/{total} steps approved.")
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
    if PERMISSION_STYLE == "codex" and sys.stdout.isatty():
        render_codex_permission_prompt(command or "", selected)
        return
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
    if "prompt_top_row" in state and "prompt_bottom_row" in state:
        try:
            top = int(state.get("prompt_top_row", "1") or 1)
            bottom = int(state.get("prompt_bottom_row", str(terminal_height())) or terminal_height())
        except ValueError:
            top = 1
            bottom = terminal_height()
        for row in range(max(1, top), min(terminal_height(), bottom) + 1):
            sys.stdout.write(f"\x1b[{row};1H\x1b[2K")
        sys.stdout.write(f"\x1b[{max(1, top)};1H")
        sys.stdout.flush()
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
    prepare_output_above_claude_footer(state)
    print(f"{transcript_bullet()} User approved {AGENT_DISPLAY_NAME}'s request")
    print()
    result, elapsed = run_with_agent_working_status(command, use_real)
    print(f"{transcript_bullet()} Bash({command})")
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
    prepare_output_above_claude_footer(state)
    print(f"{transcript_bullet()} Cancelled.")
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
    state.pop("claude_shortcuts_visible", None)


def claude_status_lines(state: dict[str, str] | None) -> list[str]:
    if state and state.get("claude_shortcuts_visible") == "1":
        return list(CLAUDE_SHORTCUT_LINES)
    if state and "claude_mode_index" in state:
        return [claude_mode_status_line(state)]
    return [CLAUDE_DEFAULT_STATUS_LINE]


def live_composer_status_lines(armed_exit: bool = False, state: dict[str, str] | None = None) -> list[str]:
    # After the first Ctrl-C, real Claude replaces the status hint with this exact
    # text until the next key (a second Ctrl-C then exits).
    if armed_exit:
        return ["Press Ctrl-C again to exit"]
    if PERMISSION_STYLE != "codex":
        if state and state.get("claude_working") == "1":
            return [state.get("claude_working_footer_status") or "  ⏸ plan mode on (shift+tab to cycle) · esc to interrupt"]
        return claude_status_lines(state)
    return [f"  {MODEL} {EFFORT} · {display_cwd()}"]


def live_composer_status_line(armed_exit: bool = False, state: dict[str, str] | None = None) -> str:
    return live_composer_status_lines(armed_exit, state)[0]


def codex_queued_prompt_status_line(width: int) -> str:
    left = "  " + CODEX_QUEUE_HINT
    right = CODEX_CONTEXT_LEFT
    if width <= len(left):
        return left[:width]
    gap = max(1, width - len(left) - len(right))
    return left + (" " * gap) + right


def live_composer_suggestion() -> str:
    if PERMISSION_STYLE == "codex":
        return "Explain this codebase"
    return 'Try "fix typecheck errors"'


def live_composer_uses_separators(status_count: int = 1) -> bool:
    return PERMISSION_STYLE != "codex" and terminal_height() >= max(8, status_count + 4)


def live_composer_layout(armed_exit: bool = False, state: dict[str, str] | None = None) -> tuple[int, int, list[str]]:
    height = terminal_height()
    status_lines = live_composer_status_lines(armed_exit, state)
    status_count = max(1, len(status_lines))
    if height <= 1:
        return 1, 1, status_lines[:1]
    if PERMISSION_STYLE == "codex":
        # Codex owns the active composer as a bottom footer in both idle and working
        # states so transcript output cannot scroll over the model/status rows.
        footer_top = max(1, height - len(codex_composer_footer_lines()) + 1)
        prompt_row = min(height, footer_top + 1)
        status_start = min(height, footer_top + 3)
        return prompt_row, status_start, status_lines[:1]
    if live_composer_uses_separators(status_count):
        status_start = max(1, height - status_count + 1)
        prompt_row = max(1, status_start - 2)
        return prompt_row, status_start, status_lines
    status_start = min(height, max(1, height - status_count + 1))
    prompt_row = max(1, status_start - 1)
    return prompt_row, status_start, status_lines


def live_composer_rows() -> tuple[int, int]:
    prompt_row, status_start, _status_lines = live_composer_layout()
    return prompt_row, status_start


def live_composer_separator_rows(armed_exit: bool = False, state: dict[str, str] | None = None) -> list[int]:
    prompt_row, status_start, status_lines = live_composer_layout(armed_exit, state)
    if not live_composer_uses_separators(len(status_lines)):
        return []
    rows: list[int] = []
    if prompt_row > 1:
        rows.append(prompt_row - 1)
    if status_start - prompt_row > 1:
        rows.append(status_start - 1)
    return rows


def live_composer_separator_line() -> str:
    return ANSI_DIM + ("─" * terminal_width()) + ANSI_RESET


def live_composer_footer_top(text: str = "", armed_exit: bool = False, state: dict[str, str] | None = None) -> int:
    if PERMISSION_STYLE == "codex":
        return max(1, terminal_height() - len(codex_composer_footer_lines(text, 0, armed_exit, state)) + 1)
    prompt_row, status_start, status_lines = live_composer_layout(armed_exit, state)
    rows = [prompt_row, status_start]
    rows.extend(status_start + index for index in range(len(status_lines)))
    separator_rows = live_composer_separator_rows(armed_exit, state)
    rows.extend(separator_rows)
    return max(1, min(rows))


def clear_live_composer_footer(text: str = "", armed_exit: bool = False, state: dict[str, str] | None = None) -> int:
    footer_top = live_composer_footer_top(text, armed_exit, state)
    previous_top = footer_top
    if state is not None:
        try:
            previous_top = int(state.get("live_composer_footer_top", str(footer_top)) or footer_top)
        except ValueError:
            previous_top = footer_top
    clear_top = max(1, min(previous_top, footer_top))
    for row in range(clear_top, terminal_height() + 1):
        sys.stdout.write(f"\x1b[{row};1H\x1b[2K")
    if state is not None:
        state["live_composer_footer_top"] = str(footer_top)
    return footer_top


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
    if PERMISSION_STYLE == "codex" and text and state and state.get("codex_working") == "1":
        status_display = ANSI_DIM + codex_queued_prompt_status_line(width) + ANSI_RESET
    else:
        status_display = clipped(live_composer_status_line(armed_exit, state), width)
    return prompt_display, status_display, cursor_col


def render_live_composer(text: str, cursor: int, armed_exit: bool = False, state: dict[str, str] | None = None) -> None:
    if PERMISSION_STYLE == "codex":
        footer_top = clear_live_composer_footer(text, armed_exit, state)
        footer_lines = codex_composer_footer_lines(text, cursor, armed_exit, state)
        prompt_row, _status_start, _status_lines = live_composer_layout(armed_exit, state)
        _prompt_display, _status_display, cursor_col = composer_render_parts(text, cursor, armed_exit, state)
        for offset, line in enumerate(footer_lines):
            row = footer_top + offset
            if row <= terminal_height():
                sys.stdout.write(f"\x1b[{row};1H\x1b[2K{line}")
        sys.stdout.write(f"\x1b[{prompt_row};{cursor_col}H")
        set_output_region_above_live_composer(text, armed_exit, state, preserve_cursor=True)
        sys.stdout.flush()
        return

    prompt_row, status_start, status_lines = live_composer_layout(armed_exit, state)
    prompt_display, status_display, cursor_col = composer_render_parts(text, cursor, armed_exit, state)
    clear_live_composer_footer(text, armed_exit, state)
    separator = live_composer_separator_line()
    separator_rows = live_composer_separator_rows(armed_exit, state)
    for row in separator_rows:
        sys.stdout.write(f"\x1b[{row};1H\x1b[2K{separator}")
    sys.stdout.write(f"\x1b[{prompt_row};1H\x1b[2K{prompt_display}")
    for index, status_line in enumerate(status_lines):
        row = status_start + index
        if row <= terminal_height():
            display = status_display if index == 0 else clipped(status_line, terminal_width())
            sys.stdout.write(f"\x1b[{row};1H\x1b[2K{display}")
    sys.stdout.write(f"\x1b[{prompt_row};{cursor_col}H")
    set_output_region_above_live_composer(text, armed_exit, state, preserve_cursor=True)
    sys.stdout.flush()


def render_inline_composer(
    text: str,
    cursor: int,
    armed_exit: bool = False,
    state: dict[str, str] | None = None,
) -> None:
    prompt_display, status_display, cursor_col = composer_render_parts(text, cursor, armed_exit, state)
    sys.stdout.write("\r\x1b[2K" + prompt_display)
    sys.stdout.write("\n\r\x1b[2K")
    sys.stdout.write("\n\r\x1b[2K" + status_display)
    sys.stdout.write(f"\x1b[2A\x1b[{cursor_col}G")
    sys.stdout.flush()


def reset_terminal_scroll_region(preserve_cursor: bool = False) -> None:
    if preserve_cursor:
        sys.stdout.write("\x1b7\x1b[r\x1b8")
    else:
        sys.stdout.write("\x1b[r")
    sys.stdout.flush()


def terminal_display_line_count(lines: list[str]) -> int:
    width = max(1, terminal_width())
    count = 0
    for line in lines:
        length = visible_len(line)
        count += max(1, ((max(1, length) - 1) // width) + 1)
    return max(0, count)


def terminal_owned_clear_top(
    state: dict[str, str] | None = None,
    next_output_line_count: int = 0,
) -> int:
    next_output_row = max(1, terminal_height() - max(0, next_output_line_count))
    clear_top = min(next_output_row, live_composer_footer_top("", False, state))
    if state is not None and "prompt_top_row" in state:
        try:
            clear_top = min(clear_top, int(state.get("prompt_top_row", str(clear_top)) or clear_top))
        except ValueError:
            pass
    return max(1, min(terminal_height(), clear_top))


def prepare_terminal_for_shell(
    next_output_line_count: int = 0,
    state: dict[str, str] | None = None,
) -> None:
    next_output_row = max(1, terminal_height() - max(0, next_output_line_count))
    clear_top = terminal_owned_clear_top(state, next_output_line_count)
    sys.stdout.write(f"\x1b[r\x1b[{clear_top};1H\x1b[J\x1b[{next_output_row};1H")
    sys.stdout.flush()


def live_composer_output_bottom(
    text: str = "",
    armed_exit: bool = False,
    state: dict[str, str] | None = None,
) -> int:
    return max(1, live_composer_footer_top(text, armed_exit, state) - 1)


def set_output_region_above_live_composer(
    text: str = "",
    armed_exit: bool = False,
    state: dict[str, str] | None = None,
    preserve_cursor: bool = True,
) -> int:
    bottom = live_composer_output_bottom(text, armed_exit, state)
    if preserve_cursor:
        sys.stdout.write(f"\x1b7\x1b[1;{bottom}r\x1b8")
    else:
        sys.stdout.write(f"\x1b[1;{bottom}r")
    if state is not None:
        state["live_composer_output_bottom"] = str(bottom)
    sys.stdout.flush()
    return bottom


def reserve_output_region_above_live_composer(state: dict[str, str] | None = None) -> None:
    bottom = set_output_region_above_live_composer("", False, state, preserve_cursor=False)
    sys.stdout.write(f"\x1b[{bottom};1H")
    sys.stdout.flush()


def clear_output_region_above_live_composer(state: dict[str, str] | None = None) -> int:
    bottom = set_output_region_above_live_composer("", False, state, preserve_cursor=False)
    for row in range(1, bottom + 1):
        sys.stdout.write(f"\x1b[{row};1H\x1b[2K")
    sys.stdout.write(f"\x1b[{bottom};1H")
    sys.stdout.flush()
    return bottom


def prepare_output_above_live_composer_footer(state: dict[str, str] | None = None) -> None:
    if not sys.stdout.isatty():
        return
    render_live_composer("", 0, state=state)
    reserve_output_region_above_live_composer(state)


def commit_live_composer_text(text: str, state: dict[str, str] | None = None) -> None:
    if not text.strip():
        return
    if PERMISSION_STYLE == "codex" and state is not None and state.pop("codex_clear_startup_on_first_submit", "") == "1":
        clear_output_region_above_live_composer(state)
    bottom = set_output_region_above_live_composer("", False, state, preserve_cursor=False)
    prompt_display, _status_display, _cursor_col = composer_render_parts(text, len(text), state=state)
    sys.stdout.write(f"\x1b[{bottom};1H\x1b[2K{prompt_display}\n")
    if PERMISSION_STYLE == "codex":
        sys.stdout.write("\x1b[2K\n")
    sys.stdout.flush()


def finish_live_composer(text: str, state: dict[str, str] | None = None) -> None:
    commit_live_composer_text(text, state)
    render_live_composer("", 0, state=state)
    reserve_output_region_above_live_composer(state)


def prepare_output_above_claude_footer(state: dict[str, str]) -> None:
    prepare_output_above_live_composer_footer(state)


def clear_live_composer(state: dict[str, str] | None = None) -> None:
    reset_terminal_scroll_region()
    if PERMISSION_STYLE != "codex":
        clear_live_composer_footer(state=state)
        prompt_row, _status_start, _status_lines = live_composer_layout(state=state)
    else:
        prompt_row = clear_live_composer_footer(state=state)
    sys.stdout.write(f"\x1b[{prompt_row};1H")
    sys.stdout.flush()


def clear_inline_composer() -> None:
    sys.stdout.write("\r\x1b[2K\n\r\x1b[2K\n\r\x1b[2K\x1b[2A\r")
    sys.stdout.flush()


def finish_inline_composer(text: str, state: dict[str, str] | None = None) -> None:
    if text.strip():
        prompt_display, _status_display, _cursor_col = composer_render_parts(text, len(text))
        sys.stdout.write("\r\x1b[2K" + prompt_display)
        sys.stdout.write("\n\r\x1b[2K")
        sys.stdout.write("\n\r\x1b[2K")
    else:
        clear_inline_composer()
    sys.stdout.flush()


def history_item(index: int) -> str:
    return readline.get_history_item(index) or ""


def read_live_composer(state: dict[str, str] | None = None) -> str:
    set_output_region_above_live_composer(state=state, preserve_cursor=True)
    text = ""
    if state is not None:
        text = state.pop("composer_prefill", "")
    cursor = len(text)
    history_count = readline.get_current_history_length()
    history_index = history_count + 1
    draft = ""
    inline_composer = False
    old_settings = termios.tcgetattr(sys.stdin.fileno())
    try:
        tty.setraw(sys.stdin.fileno())
        while True:
            armed_exit = bool(state and state.get("last_ctrl_c_at"))
            if inline_composer:
                render_inline_composer(text, cursor, armed_exit, state)
            else:
                render_live_composer(text, cursor, armed_exit, state)
            key = read_key()
            if state is not None and key != "\x03":
                clear_ctrl_c_exit_window(state)
            if key == "\x1b[Z" and state is not None and not inline_composer:
                cycle_claude_mode(state)
                continue
            if key == "?" and state is not None and not inline_composer and not text:
                state["claude_shortcuts_visible"] = "1"
                continue
            if key in {"\r", "\n"}:
                if inline_composer:
                    finish_inline_composer(text, state)
                else:
                    finish_live_composer(text, state)
                if text.strip():
                    readline.add_history(text)
                return text
            if key == "\x03":
                if inline_composer:
                    clear_inline_composer()
                else:
                    clear_live_composer(state)
                raise KeyboardInterrupt
            if key == "\x04":
                if text:
                    text = text[:cursor] + text[cursor + 1:]
                    continue
                if inline_composer:
                    clear_inline_composer()
                else:
                    clear_live_composer(state)
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
                if state is not None and not inline_composer:
                    state.pop("claude_shortcuts_visible", None)
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


def codex_exit_footer_lines(state: dict[str, str]) -> list[str]:
    return [
        codex_token_usage_line(state),
        f"To continue this session, run codex resume {codex_session_id(state)}",
    ]


def codex_exit_footer_display_line_count(state: dict[str, str]) -> int:
    return terminal_display_line_count(codex_exit_footer_lines(state))


def print_codex_exit_footer(state: dict[str, str]) -> None:
    for line in codex_exit_footer_lines(state):
        print(line)


def print_exit_message(state: dict[str, str]) -> None:
    if AGENT_NAME == "codex":
        print_codex_exit_footer(state)
    else:
        print(f"{transcript_bullet()} Goodbye.")


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


def mock_fixture_list_relevant(case: dict[str, object], include_shared: bool = False) -> bool:
    agent = str(case.get("agent") or "").strip().lower()
    if agent == AGENT_NAME:
        return True
    if include_shared and (not agent or agent in {"unknown", "generic"}):
        return True
    return False


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


def mock_fixture_occupies_screen(case: dict[str, object], group: dict[str, object] | None, freeze_static: bool) -> bool:
    return mock_fixture_requires_screen(case, group, freeze_static)


def mock_fixture_list_cases(
    include_shared: bool = False,
    include_idle: bool = False,
    only_idle: bool = False,
) -> list[dict[str, object]]:
    seen: set[tuple[str, str, str]] = set()
    visible_cases: list[dict[str, object]] = []
    sorted_cases = sorted(load_mock_fixture_cases(), key=lambda c: (str(c.get("case_name") or "").lower(), str(c.get("agent") or ""), str(c.get("path") or "")))
    relevant_cases = [case for case in sorted_cases if mock_fixture_list_relevant(case, include_shared)]
    real_labels = {mock_fixture_label_key(case) for case in relevant_cases if mock_fixture_is_real_capture(case)}
    for case in relevant_cases:
        outcome = mock_fixture_outcome_label(case)
        if only_idle and outcome != "idle":
            continue
        if outcome == "idle" and not (include_idle or only_idle):
            continue
        if not mock_fixture_is_real_capture(case) and mock_fixture_label_key(case) in real_labels:
            continue
        key = mock_fixture_list_key(case)
        if key in seen:
            continue
        seen.add(key)
        visible_cases.append(case)
    return visible_cases


def print_mock_fixture_list(
    include_shared: bool = False,
    include_idle: bool = False,
    only_idle: bool = False,
) -> None:
    print(f"{transcript_bullet()} Mock fixture cases")
    width = terminal_width()
    for case in mock_fixture_list_cases(include_shared, include_idle, only_idle):
        agent = str(case.get("agent") or "generic")
        outcome = mock_fixture_outcome_label(case)
        cursor = case.get("cursor") if isinstance(case.get("cursor"), dict) else {}
        line = f"  ⎿  {agent}: [{outcome}] {case['case_name']} ({Path(case['path']).name}) {mock_fixture_cursor_label(cursor)}"
        print(clipped(line, width))
    print()


def print_mock_fixture_dump() -> None:
    cases = mock_fixture_list_cases(include_shared=True, include_idle=True)
    total = len(cases)
    for index, case in enumerate(cases, start=1):
        path = Path(case.get("path") or "")
        agent = str(case.get("agent") or "generic")
        outcome = mock_fixture_outcome_label(case)
        case_name = str(case.get("case_name") or path.stem)
        print(f"===== BEGIN FIXTURE {index}/{total}: {path.name} =====")
        print(f"agent: {agent}")
        print(f"case: {case_name}")
        print(f"outcome: {outcome}")
        print(f"path: {path}")
        print("----- capture -----")
        capture = str(case.get("styled_capture") or case.get("raw_capture") or "")
        if capture:
            sys.stdout.write(capture)
            if not capture.endswith("\n"):
                print()
        print("===== END FIXTURE: " + path.name + " =====")
        if index != total:
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


def render_plain_fixture_lines(lines: list[str], state: dict[str, str]) -> None:
    if PERMISSION_STYLE == "codex":
        if lines:
            sys.stdout.write("\n")
            sys.stdout.write("\n".join(lines))
            sys.stdout.write("\n")
        return
    footer_top = live_composer_footer_top("", False, state)
    available_bottom = max(1, footer_top - 1)
    visible_lines = lines[-available_bottom:]
    if not visible_lines:
        return
    start_row = max(1, available_bottom - len(visible_lines) + 1)
    for row in range(start_row, available_bottom + 1):
        sys.stdout.write(f"\x1b[{row};1H\x1b[2K")
    for offset, line in enumerate(visible_lines):
        sys.stdout.write(f"\x1b[{start_row + offset};1H{line}")


def mock_fixture_requires_screen(case: dict[str, object], group: dict[str, object] | None, freeze_static: bool) -> bool:
    if group or freeze_static:
        return True
    expected = case.get("expected") if isinstance(case.get("expected"), dict) else {}
    screen_key = str(expected.get("screen_key") or "").strip()
    reason_code = str(expected.get("reason_code") or "").strip()
    return bool(expected.get("ask") is True or expected.get("approval_visible") is True or screen_key in {"approval", "needs-input", "working"} or reason_code == "busy")


def fixture_reserves_footer(case: dict[str, object], occupies_screen: bool, group: dict[str, object] | None, freeze_static: bool) -> bool:
    if not occupies_screen or freeze_static:
        return False
    if PERMISSION_STYLE != "codex":
        return True
    expected = case.get("expected") if isinstance(case.get("expected"), dict) else {}
    screen_key = str(expected.get("screen_key") or "").strip()
    return bool(group or expected.get("ask") is True or expected.get("approval_visible") is True or screen_key in {"approval", "needs-input"})


def fixture_render_height(state: dict[str, str], reserve_footer: bool) -> int:
    if reserve_footer:
        return max(1, live_composer_footer_top("", False, state) - 1)
    return terminal_height()


def strip_codex_captured_footer(lines: list[str], reserve_footer: bool) -> list[str]:
    if PERMISSION_STYLE != "codex" or not reserve_footer:
        return lines
    stripped = list(lines)
    while stripped and not plain_capture_line(stripped[-1]).strip():
        stripped.pop()
    if not stripped or not CODEX_CAPTURED_STATUS_RE.match(plain_capture_line(stripped[-1]).strip()):
        return lines
    stripped.pop()
    while stripped and not plain_capture_line(stripped[-1]).strip():
        stripped.pop()
    if stripped and CODEX_CAPTURED_PROMPT_RE.match(plain_capture_line(stripped[-1]).strip()):
        stripped.pop()
    while stripped and not plain_capture_line(stripped[-1]).strip():
        stripped.pop()
    return stripped


def render_fixture_screen(
    lines: list[str],
    top_padding: int,
    render_height: int,
    state: dict[str, str],
    reserve_footer: bool,
) -> None:
    if reserve_footer:
        for row in range(1, render_height + 1):
            sys.stdout.write(f"\x1b[{row};1H\x1b[2K")
        start_row = top_padding + 1
        for offset, line in enumerate(lines):
            row = start_row + offset
            if row <= render_height:
                sys.stdout.write(f"\x1b[{row};1H{line}")
        render_live_composer("", 0, state=state)
        return
    sys.stdout.write("\x1b[H\x1b[J")
    if top_padding:
        sys.stdout.write("\n" * top_padding)
    # Render WITHOUT a trailing newline: a trailing "\n" on the bottom-most row
    # scrolls the whole frame up one line, which would throw off the absolute row
    # math the interactive handler uses to move the selector.
    sys.stdout.write("\n".join(lines))


def cmd_mock_fixture(state: dict[str, str], name: str, freeze_static: bool = False) -> None:
    case = find_mock_fixture_case(name)
    if case is None:
        print(f"{transcript_bullet()} Unknown mock fixture case: {name}")
        print()
        print_mock_fixture_list()
        return
    if not freeze_static and mock_fixture_is_codex_live_working(case) and sys.stdin.isatty() and sys.stdout.isatty():
        if mock_fixture_is_codex_goal_active(case):
            start_codex_goal_active_mock(state, case)
        else:
            start_codex_live_working_mock(state, case)
        return
    if not freeze_static and mock_fixture_is_claude_live_working(case) and sys.stdin.isatty() and sys.stdout.isatty():
        start_claude_live_working_mock(state, case)
        return
    if not freeze_static and mock_fixture_is_codex_draft_only(case) and sys.stdin.isatty() and sys.stdout.isatty():
        start_codex_draft_mock(state, case)
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
    provisional_group = fixture_choice_group(clipped_lines) if (sys.stdin.isatty() and mock_fixture_allows_choice_interaction(case)) else None
    occupies_screen = mock_fixture_occupies_screen(case, provisional_group, freeze_static)
    reserve_footer = fixture_reserves_footer(case, occupies_screen, provisional_group, freeze_static)
    clipped_lines = strip_codex_captured_footer(clipped_lines, reserve_footer)
    provisional_group = fixture_choice_group(clipped_lines) if (sys.stdin.isatty() and mock_fixture_allows_choice_interaction(case)) else None
    occupies_screen = mock_fixture_occupies_screen(case, provisional_group, freeze_static)
    reserve_footer = fixture_reserves_footer(case, occupies_screen, provisional_group, freeze_static)
    render_height = fixture_render_height(state, reserve_footer)
    drop = max(0, len(clipped_lines) - render_height)
    lines = clipped_lines[drop:]
    line_count = len(lines)
    # If this capture is a live numbered-choice prompt, let the user actually drive it
    # (arrow keys / digits / Enter / Esc) instead of just freezing the pane. We only do
    # so when the choice block round-trips byte-for-byte under our selector rewrite, so
    # the initial frame stays identical to the capture.
    group = fixture_choice_group(lines) if (sys.stdin.isatty() and mock_fixture_allows_choice_interaction(case)) else None
    # A frozen (mockcase) or interactive (choice) fixture owns the output region.
    # Claude still keeps its composer/status footer reserved at the bottom, so
    # interactive fixtures render only above that footer.
    top_padding = max(0, render_height - line_count) if (occupies_screen and line_count and line_count < render_height) else 0
    if occupies_screen:
        render_fixture_screen(lines, top_padding, render_height, state, reserve_footer)
    else:
        render_plain_fixture_lines(lines, state)
    state["fixture_case"] = str(case.get("case_name") or name)
    render_cursor = mock_fixture_render_cursor(lines, cursor, render_height, top_padding, group, drop)
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
    elif occupies_screen and not reserve_footer:
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
    message = f"{transcript_bullet()} Cancelled."
    if action == "select":
        choice = re.sub(r"^\d+\.\s*", "", bodies[selected].strip())
        message = f"{transcript_bullet()} User answered: {choice}"
    if PERMISSION_STYLE != "codex":
        footer_top = live_composer_footer_top("", False, state)
        result_row = max(1, min(footer_top - 1, bottom_row + 1))
        sys.stdout.write(f"\x1b[{result_row};1H\x1b[2K{message}")
        clear_pending(state)
        render_live_composer("", 0, state=state)
        return
    sys.stdout.write(f"\x1b[{bottom_row};1H")
    sys.stdout.flush()
    print()
    print(message)
    print()
    clear_pending(state)


def cmd_help() -> None:
    print(f"{transcript_bullet()} Keyboard shortcuts")
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
    print(f"{transcript_bullet()} Slash commands")
    print("  ⎿  /help             Show this help")
    print("     /clear            Clear screen and reset session")
    print("     /status           Session status")
    print("     /exit, /quit      Exit")
    print()
    print(f"{transcript_bullet()} Mock-only triggers (not real {AGENT_DISPLAY_NAME})")
    print('  ⎿  <shell cmd>       Permission prompt, then REAL shell exec on Yes')
    print('     exec <cmd>        Same, for an arbitrary command')
    print('     !<cmd>            Bash mode — REAL shell exec, NO permission prompt')
    print('     sleep N           Real sleep behind a permission prompt (working state)')
    print('     yesno [N]         Mock build script — N Yes/No prompts in a row (default 3)')
    print('     mock <case>       Render a fixture; drive options (↑/↓, 1-9, Enter, Esc/Ctrl-C)')
    print('     mock list         List this agent\'s non-idle fixture cases (also: mock, mocklist)')
    print('     mock list all     Include shared generic and idle detector fixtures')
    print('     mock list idle    Show idle negative detector fixtures')
    print('     mockcase, case    Aliases for mock')
    print('     ask, question     AskUserQuestion demo (arrow-key choice)')
    print('     todos             Ctrl-T style task-list overlay')
    print()


def cmd_clear(state: dict[str, str]) -> None:
    sys.stdout.write("\x1b[H\x1b[J")
    sys.stdout.flush()
    state.clear()
    print_startup(state)


def cmd_status(state: dict[str, str]) -> None:
    print(f"{transcript_bullet()} Session status")
    print(f"  ⎿  Model:     {MODEL}")
    print(f"     Effort:    {EFFORT}")
    print(f"     Cwd:       {os.getcwd()}")
    print(f"     Turn:      {state.get('turn', '0')}")
    print(f"     Tokens in: {state.get('tokens_in', '0')}")
    print(f"     Tokens out:{state.get('tokens_out', '0')}")
    print()


def cmd_bang_bash(command: str) -> None:
    print_thinking(seconds=1, tokens=12)
    print(f"{transcript_bullet()} Bash({command})  (bash mode)")
    result = real_exec(command)
    lines = result.split("\n") if result else [""]
    for i, line in enumerate(lines):
        prefix = "  ⎿  " if i == 0 else "     "
        print(f"{prefix}{line}")
    print()


def sleep_duration_text(raw_seconds: str) -> tuple[float, str]:
    seconds = max(0.0, float(raw_seconds))
    if seconds.is_integer():
        display = str(int(seconds))
    else:
        display = f"{seconds:g}"
    unit = "second" if seconds == 1 else "seconds"
    return seconds, f"{display} {unit}"


def cmd_codex_sleep(raw_seconds: str) -> str:
    seconds, label = sleep_duration_text(raw_seconds)
    command = f"sleep {raw_seconds}"
    print(f"• Running {command} now.")
    print()
    if sys.stdout.isatty():
        sys.stdout.write("\n" * len(codex_working_block_lines(0)))
        sys.stdout.flush()
    queued_text = print_codex_working(seconds, background=True)
    print(f"• Ran {command}")
    print("  └ (no output)")
    print()
    print(full_rule(terminal_width()))
    print()
    print("• Done.")
    print()
    print(full_rule(terminal_width()))
    print()
    return queued_text


def cmd_todos() -> None:
    print_thinking(seconds=2)
    todos = [
        ("☒", "Survey current mock fidelity"),
        ("☒", "Add thinking-verb rotation"),
        ("☐", "Wire slash commands"),
        ("☐", "Mock more tool types"),
        ("☐", "Polish conversational fallback"),
    ]
    print(f"{transcript_bullet()} Update Todos")
    for i, (mark, text) in enumerate(todos):
        prefix = "  ⎿  " if i == 0 else "     "
        print(f"{prefix}{mark} {text}")
    print()


def print_capabilities() -> None:
    print(f"{transcript_bullet()} Here's what I can actually do (mock — these all work for real):")
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
    print("    mock list          ← list this agent's non-idle fixture cases (mockcase/case also work)")
    print("    mock list all      ← include shared generic and idle detector fixtures")
    print("    mock list idle     ← show idle negative detector fixtures")
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
        elif mock_fixture_key(name) in {"list_all", "ls_all", "all"}:
            print_mock_fixture_list(include_shared=True, include_idle=True)
        elif mock_fixture_key(name) in {"list_idle", "ls_idle", "idle"}:
            print_mock_fixture_list(include_idle=True, only_idle=True)
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

    # sleep / wait N — Codex treats this like a normal turn; Claude keeps the approval prompt path.
    m = re.match(r"^(sleep|wait)\s+(\d+(?:\.\d+)?)", value, re.IGNORECASE)
    if m:
        secs = m.group(2)
        if PERMISSION_STYLE == "codex":
            queued_text = cmd_codex_sleep(secs)
            if queued_text:
                state["composer_prefill"] = queued_text
            return
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
    state: dict[str, str] = {}
    print_startup(state)
    while True:
        try:
            if state.get("pending") == "permission" and sys.stdin.isatty():
                handle_pending_permission_tty(state)
                continue
            if state.get("pending") == "question" and sys.stdin.isatty():
                handle_pending_question_tty(state)
                continue
            if state.get("pending") == "claude-working" and sys.stdin.isatty():
                handle_claude_live_working_tty(state)
                continue
            if state.get("pending") in {"codex-working", "codex-goal-active"} and sys.stdin.isatty():
                handle_codex_live_working_tty(state)
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
            next_lines = codex_exit_footer_display_line_count(state) if AGENT_NAME == "codex" else 0
            prepare_terminal_for_shell(next_lines, state)
            if AGENT_NAME == "codex":
                print_codex_exit_footer(state)
            sys.exit(0)
        except EOFError:
            next_lines = codex_exit_footer_display_line_count(state) if AGENT_NAME == "codex" else 0
            prepare_terminal_for_shell(next_lines, state)
            if AGENT_NAME == "codex":
                print_codex_exit_footer(state)
            sys.exit(0)


if __name__ == "__main__":
    main()
