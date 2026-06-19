#!/usr/bin/env python3
"""Shared mock agent terminal for YOLOmux UI testing."""

import atexit
import os
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

FRAMES = ["✻", "✶", "✷", "✸", "✹", "✺"]
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
) -> None:
    global AGENT_NAME, AGENT_DISPLAY_NAME, AGENT_PRODUCT_NAME, HISTORY_FILE
    global VERSION, MODEL, EFFORT, MODEL_LINE, PROMPT_GLYPH, SELECTOR_GLYPH, PERMISSION_STYLE
    global STARTUP_STYLE

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
    return max(88, min(shutil.get_terminal_size((DEFAULT_WIDTH, 24)).columns, 150))


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
    if os.path.realpath(os.getcwd()) == os.path.realpath(os.path.expanduser("~")):
        print_minimal_header()
    else:
        print_welcome_box()
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
WELCOME_ORG_LINE = "Keiven Chang - Claude"
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

    title = f"{AGENT_PRODUCT_NAME}{ANSI_DIM}  v{VERSION}{ANSI_RESET}"
    welcome = f"{ANSI_BOLD}Welcome back {user_name}!{ANSI_RESET}"

    indent = "        "
    # The body row (▝▜█████▛▘, 9 cells) is the widest; center the narrower head
    # (7) and legs (5) under it with per-row offsets (+1 / +2) rather than a
    # uniform indent, so the three rows stack into one robot.
    robot = [
        f"{indent} {CLAUDE_ORANGE}▐▛███▜▌{ANSI_RESET}",
        f"{indent}{CLAUDE_ORANGE}▝▜█████▛▘{ANSI_RESET}",
        f"{indent}  {CLAUDE_ORANGE}▘▘ ▝▝{ANSI_RESET}",
    ]
    enterprise = f"{ANSI_DIM}{WELCOME_PLAN_LABEL}{ANSI_RESET}"
    gap = max(2, left_w - 3 - visible_len(robot[1]) - visible_len(enterprise))
    robot_mid = robot[1] + (" " * gap) + enterprise

    left_lines = [
        title,
        center_cell(welcome, left_w),
        robot[0],
        robot_mid,
        robot[2],
        "",
        f" {ANSI_DIM}{MODEL_LINE}{ANSI_RESET}",
        f" {ANSI_DIM}{WELCOME_ORG_LINE}{ANSI_RESET}",
        f" {ANSI_DIM}{display_cwd()}{ANSI_RESET}",
    ]

    tip = f"Run /init to create a CLAUDE.md file with instructions for {AGENT_DISPLAY_NAME}"
    whats_new = [
        "Internal infrastructure improvements (no user-facing changes)",
        "Auto mode is now available on Bedrock, Vertex AI, and more",
        "Plugins in `.claude/skills` directories are now supported",
    ]
    right_lines = [
        f"{CLAUDE_ORANGE}{ANSI_BOLD}Tips for getting started{ANSI_RESET}",
        tip,
        "",
        f"{CLAUDE_ORANGE}{ANSI_BOLD}What's new{ANSI_RESET}",
        whats_new[0],
        whats_new[1],
        whats_new[2],
        f"{ANSI_DIM}{ANSI_ITALIC}/release-notes for more{ANSI_RESET}",
        "",
    ]

    print("╭" + ("─" * inner) + "╮")
    for left, right in zip(left_lines, right_lines):
        print("│" + pad_cell(left, left_w) + "│" + pad_cell(" " + right, right_w) + "│")
    print("╰" + ("─" * inner) + "╯")


def print_codex_startup() -> None:
    inner = 45

    def box_line(text: str = "") -> str:
        return "│" + clipped(text, inner) + "│"

    print("╭" + ("─" * inner) + "╮")
    print(box_line(f" >_ {AGENT_PRODUCT_NAME} (v{VERSION})"))
    print(box_line())
    print(box_line(f" model:     {MODEL} {EFFORT}   /model to change"))
    print(box_line(f" directory: {display_cwd()}"))
    print("╰" + ("─" * inner) + "╯")
    print()
    print("  Tip: NEW: Codex can now generate and use memories. Try it now with /memories")
    print()
    print()
    print(f"{PROMPT_GLYPH} Implement {{feature}}")
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
    total = max(1, int(seconds))
    minutes, remaining = divmod(total, 60)
    if minutes:
        return f"{minutes}m {remaining}s"
    return f"{remaining}s"


def codex_working_word(frame: int, color: bool = True) -> str:
    word = "Working"
    if not color:
        return word
    active = frame % len(word)
    parts = []
    for index, char in enumerate(word):
        color = "\x1b[97m" if index == active else "\x1b[90m"
        parts.append(f"{color}{char}")
    return "".join(parts) + "\x1b[0m"


def print_codex_working(seconds: float) -> None:
    if not sys.stdout.isatty():
        print(f"• Working ({format_working_elapsed(seconds)} • esc to interrupt)")
        return

    tick = 0.12
    total_ticks = max(1, int(seconds / tick))
    for i in range(total_ticks + 1):
        elapsed = min(seconds, i * tick)
        line = f"• {codex_working_word(i)} ({format_working_elapsed(elapsed)} • esc to interrupt)"
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
    return [f"• {codex_working_word(frame, sys.stdout.isatty())} ({format_working_elapsed(elapsed)} • esc to interrupt)"]


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
                raise KeyboardInterrupt
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
        print(" Codex wants to run a shell command"); n += 1
        print(); n += 1
        print(" Would you like to run the following command?"); n += 1
        print(); n += 1
        # Real Codex prints the Reason FIRST, then the command on a `$ `-prefixed line (continuations
        # indented under it). The detector's Codex extractor keys on that `$ ` line and stops at the
        # selector boundary, so the command MUST be `$ `-prefixed and the Reason MUST come before it —
        # otherwise extract_command folds the Reason into the command or returns nothing.
        print(f"   Reason: {description}"); n += 1
        print(); n += 1
        for i, line in enumerate(cmd_lines):
            print(f"   {'$ ' if i == 0 else '  '}{line}"); n += 1
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
            f" {markers[0]} 1. Yes",
            f" {markers[1]} 2. Yes, and don't ask again for commands that start with `{prefix}`",
            f" {markers[2]} 3. No",
            "",
            " Esc to cancel · Tab to amend",
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
                raise KeyboardInterrupt
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


def cmd_help() -> None:
    print("● Keyboard shortcuts")
    print("  ⎿  Enter             Submit message")
    print("     Shift+Enter       New line (also \\ + Enter)")
    print("     Up/Down           Previous prompts")
    print("     Ctrl+R            Reverse search history")
    print("     Esc               Interrupt current task")
    print("     Esc Esc           Edit previous message")
    print("     Ctrl+L            Clear screen")
    print("     Ctrl+C            Cancel input / interrupt")
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
        print("● Goodbye.")
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
            pending = state.get("pending") == "permission"
            prompt = "" if pending else f"{PROMPT_GLYPH} "
            user_input = input(prompt)
            if sys.stdin.isatty():
                sys.stdout.write("\x1b[1A\x1b[2K\r")
                sys.stdout.flush()
                if not pending and user_input.strip():
                    print_user_header(user_input)
            else:
                print()
            handle_command(user_input, state)
        except KeyboardInterrupt:
            print()
            print("● Interrupted.")
            sys.exit(0)
        except EOFError:
            print()
            sys.exit(0)


if __name__ == "__main__":
    main()
