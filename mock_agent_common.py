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
    print(f"  ▐▛███▜▌   Mock {AGENT_PRODUCT_NAME} v{VERSION}")
    print(f"▝▜█████▛▘  {MODEL_LINE}")
    print(f"  ▘▘ ▝▝    {display_cwd()}")
    print()
    print_prompt_box(f'{PROMPT_GLYPH} Try "fix typecheck errors"', width)
    print()


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


def codex_working_word(frame: int) -> str:
    word = "Working"
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


def codex_working_status(stop_event: threading.Event, started_at: float) -> None:
    frame = 0
    while not stop_event.is_set():
        elapsed = max(1, time.time() - started_at)
        line = f"• {codex_working_word(frame)} ({format_working_elapsed(elapsed)} • esc to interrupt)"
        sys.stdout.write("\r\x1b[2K" + line)
        sys.stdout.flush()
        frame += 1
        stop_event.wait(0.12)


def run_with_codex_working_status(command: str, use_real: bool) -> tuple[str, int]:
    started_at = time.time()
    if not sys.stdout.isatty():
        print("• Working (1s • esc to interrupt)")
        result = real_exec(command) if use_real else result_for_command(command)
        elapsed = max(1, round(time.time() - started_at))
        return result, elapsed

    stop_event = threading.Event()
    worker = threading.Thread(target=codex_working_status, args=(stop_event, started_at), daemon=True)
    worker.start()
    try:
        result = real_exec(command) if use_real else result_for_command(command)
    finally:
        stop_event.set()
        worker.join(timeout=0.5)
    elapsed = max(1, round(time.time() - started_at))
    sys.stdout.write("\r\x1b[2K" + f"• Working ({format_working_elapsed(elapsed)} • esc to interrupt)" + "\n")
    sys.stdout.flush()
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
    print_thinking()
    n = 1  # leading blank from print_thinking
    print(f"● Bash({command})"); n += 1
    print(f"  ⎿  {preview_verb(command)}… (ctrl+o to expand)"); n += 1
    cmd_lines = textwrap.wrap(command, width=76) or [""]
    for i, line in enumerate(cmd_lines):
        prefix = "     $ " if i == 0 else "     | "
        print(f"{prefix}{line}"); n += 1
    print(); n += 1
    prompt_rule(terminal_width()); n += 1
    print(); n += 1
    if PERMISSION_STYLE == "codex":
        print(" Codex wants to run a shell command"); n += 1
        print(); n += 1
        print(" Would you like to run the following command?"); n += 1
        print(); n += 1
        for line in cmd_lines:
            print(f"   {line}"); n += 1
        print(f"   Reason: {description}"); n += 1
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


def print_dynamo_rename_flow() -> tuple[str, str, int]:
    print_thinking(seconds=2)
    command = "tmux ls 2>/dev/null | grep -E '^dynamo[0-9]+:' || echo \"no dynamo sessions\""
    print_tool_error(command)
    print_tool_multiline(
        command,
        [
            "dynamo1: 2 windows (created Thu May  7 14:34:50 2026) (attached)",
            "dynamo2: 2 windows (created Wed May  6 13:46:17 2026) (attached)",
            "dynamo3: 2 windows (created Fri May  8 12:41:25 2026) (attached)",
        ],
        more=2,
    )
    print("● Found 5 sessions: dynamo1-4, dynamo6. Rename them?")
    print()
    print(f"● User answered {AGENT_DISPLAY_NAME}'s questions:")
    print("  ⎿  · Rename dynamo1, dynamo2, dynamo3, dynamo4, dynamo6 → 1, 2, 3, 4, 6? → Yes, rename all")
    print()
    rename_command = "for n in 1 2 3 4 6; do tmux rename-session -t dynamo$n $n; done && tmux ls"
    description = "Rename dynamo sessions"
    n = print_bash_prompt(rename_command, description)
    return rename_command, description, n


def set_pending_permission(state: dict[str, str], command: str, description: str, lines: int = 0) -> None:
    state["pending"] = "permission"
    state["command"] = command
    state["description"] = description
    state["selected"] = "0"
    state["prompt_lines"] = str(lines)


def clear_pending(state: dict[str, str]) -> None:
    keep = {k: v for k, v in state.items() if k in {"tokens_in", "tokens_out", "turn"}}
    state.clear()
    state.update(keep)


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
    if PERMISSION_STYLE == "codex":
        result, elapsed = run_with_codex_working_status(command, use_real)
        print(f"● Bash({command})")
    else:
        print(f"● Bash({command})")
        print("  ⎿  Running…")
        sys.stdout.flush()
        t0 = time.time()
        result = real_exec(command) if use_real else result_for_command(command)
        elapsed = max(1, round(time.time() - t0))
        if sys.stdout.isatty():
            sys.stdout.write("\x1b[1A\x1b[2K\r")
            sys.stdout.flush()
    result_lines = result.split("\n") if result else [""]
    for i, line in enumerate(result_lines):
        prefix = "  ⎿  " if i == 0 else "     "
        print(f"{prefix}{line}")
    print()
    print_done_summary(seconds=elapsed)


def cancel_pending_permission(state: dict[str, str]) -> None:
    erase_prompt_block(state)
    clear_pending(state)
    print("● Cancelled.")
    print()


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


def set_pending_guess(state: dict[str, str], target: int, tries: int) -> None:
    state["pending"] = "guess"
    state["guess_target"] = str(target)
    state["guess_tries_left"] = str(tries)
    state["guess_tries_total"] = str(tries)


def handle_pending_guess(user_input: str, state: dict[str, str]) -> bool:
    value = user_input.strip().lower()
    if value in {"quit", "stop", "cancel", "exit", "/quit", "/exit"}:
        target = state.get("guess_target", "?")
        clear_pending(state)
        print(f"● Stopped. The number was {target}.")
        print()
        return True
    try:
        guess = int(value)
    except ValueError:
        print("● Enter a number between 0 and 100, or 'quit' to stop.")
        print()
        return True
    if not 0 <= guess <= 100:
        print("● Out of range — pick a number between 0 and 100.")
        print()
        return True
    target = int(state.get("guess_target", "-1"))
    tries_left = int(state.get("guess_tries_left", "0")) - 1
    total = int(state.get("guess_tries_total", "7"))
    if guess == target:
        used = total - tries_left
        clear_pending(state)
        print(f"● You got it! {target} in {used} {'try' if used == 1 else 'tries'}.")
        print()
        return True
    if tries_left <= 0:
        clear_pending(state)
        print(f"● Out of tries. The number was {target}.")
        print()
        return True
    hint = "higher" if guess < target else "lower"
    state["guess_tries_left"] = str(tries_left)
    print(f"● {hint}. {tries_left} {'try' if tries_left == 1 else 'tries'} left.")
    print()
    return True


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
    if pending == "guess":
        return handle_pending_guess(user_input, state)
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
    print("     /exit, /quit      Exit")
    print("     /model            Current model")
    print("     /status           Session status")
    print("     /cost             Tokens & cost so far")
    print("     /compact          Summarize and compact context")
    print("     /init             Initialize agent instructions")
    print("     /agents           Pick agent")
    print("     /doctor           Diagnostics")
    print()
    print(f"● Mock-only triggers (not real {AGENT_DISPLAY_NAME})")
    print('  ⎿  exec <cmd>        Permission prompt, then REAL shell exec')
    print('     !<cmd>            Bash mode — REAL shell exec, no permission')
    print('     sleep N           Real sleep + permission prompt')
    print('     read <path>       Mock Read tool (cosmetic)')
    print('     grep <pattern>    Mock Grep tool (cosmetic)')
    print('     edit <path>       Mock Edit tool (cosmetic)')
    print('     write <path>      Mock Write tool (cosmetic)')
    print('     todos             Mock TodoWrite tool (cosmetic)')
    print('     permission/bash   Permission prompt with canned "ok" result')
    print('     dynamo rename     Multi-step tool flow demo')
    print('     ask, question     AskUserQuestion demo')
    print('     guess             20-questions demo')
    print()


def cmd_clear(state: dict[str, str]) -> None:
    sys.stdout.write("\x1b[H\x1b[J")
    sys.stdout.flush()
    state.clear()
    print_startup()


def cmd_model() -> None:
    print(f"● Current model: {MODEL}")
    print(f"  Effort: {EFFORT}")
    print(f"  Billing: API Usage")
    print()


def cmd_status(state: dict[str, str]) -> None:
    print("● Session status")
    print(f"  ⎿  Model:     {MODEL}")
    print(f"     Effort:    {EFFORT}")
    print(f"     Cwd:       {os.getcwd()}")
    print(f"     Turn:      {state.get('turn', '0')}")
    print(f"     Tokens in: {state.get('tokens_in', '0')}")
    print(f"     Tokens out:{state.get('tokens_out', '0')}")
    print()


def cmd_cost(state: dict[str, str]) -> None:
    tokens_in = int(state.get("tokens_in", "0"))
    tokens_out = int(state.get("tokens_out", "0"))
    cost = (tokens_in * 15 + tokens_out * 75) / 1_000_000
    print("● Session cost")
    print(f"  ⎿  Input tokens:  {tokens_in:>8}")
    print(f"     Output tokens: {tokens_out:>8}")
    print(f"     Estimated:     ${cost:.4f}")
    print()


def cmd_compact(state: dict[str, str]) -> None:
    print_thinking(seconds=2, tokens=128)
    print("● Compacted context. Earlier turns summarized.")
    state["tokens_in"] = "0"
    state["tokens_out"] = "0"
    print()


def cmd_init() -> None:
    print_thinking(seconds=2)
    print(f"● No AGENTS.md found. Would scan the codebase and propose one — mock {AGENT_NAME}, no file written.")
    print()


def cmd_doctor() -> None:
    print("● Diagnostics")
    print("  ⎿  ✔ MCP server: ok")
    print("     ✔ Auth: ok")
    print("     ⚠ 1 setting issue: outdated agent instructions format")
    print("     ✔ Git repo: ok")
    print()


def cmd_agents() -> None:
    print("● Available agents")
    print(f"  ⎿  {SELECTOR_GLYPH} 1. {AGENT_NAME:<15} — default")
    print("       2. Explore         — read-only search")
    print("       3. Plan            — design implementation plan")
    print("       4. general-purpose — multi-step research")
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


def cmd_read(path: str) -> None:
    print_thinking(seconds=2)
    n = random.randint(50, 800)
    print(f"● Read({path})")
    print(f"  ⎿  Read {n} lines (ctrl+o to expand)")
    print()


def cmd_grep(pattern: str, path: str = ".") -> None:
    print_thinking(seconds=2)
    matches = random.randint(0, 50)
    files = random.randint(0, min(matches, 12)) if matches else 0
    print(f'● Grep(pattern: "{pattern}", path: "{path}")')
    if matches == 0:
        print("  ⎿  No matches")
    else:
        print(f"  ⎿  Found {matches} matches in {files} files")
    print()


def cmd_edit(path: str) -> None:
    print_thinking(seconds=2)
    print(f"● Edit({path})")
    print(f"  ⎿  Applied 1 edit to {path}")
    print()


def cmd_write(path: str) -> None:
    print_thinking(seconds=2)
    n = random.randint(20, 200)
    print(f"● Write({path})")
    print(f"  ⎿  Wrote {n} lines to {path}")
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
    print("    sleep N            ← real time.sleep with permission prompt")
    print("    guess              ← number-guessing game (0-100, 7 tries)")
    print("    ask                ← AskUserQuestion demo with arrow-key nav")
    print()
    print("  Slash commands:")
    print("    /help              ← shortcut and slash-command reference")
    print("    /status /cost      ← session + token info")
    print("    /clear             ← clear screen")
    print("    /quit /exit        ← exit")
    print()
    print("  Cosmetic demos (no real action):")
    print("    read <path>, grep <pat>, edit <path>, write <path>, todos")
    print("    dynamo rename, 20 questions")
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
    if lower == "/model":
        cmd_model()
        return
    if lower == "/status":
        cmd_status(state)
        return
    if lower == "/cost":
        cmd_cost(state)
        return
    if lower == "/compact":
        cmd_compact(state)
        return
    if lower == "/init":
        cmd_init()
        return
    if lower == "/doctor":
        cmd_doctor()
        return
    if lower == "/agents":
        cmd_agents()
        return

    if value.startswith("!"):
        cmd_bang_bash(value[1:].strip() or "echo ok")
        return

    m = re.match(r"^read\s+(.+)$", value, re.IGNORECASE)
    if m:
        cmd_read(m.group(1).strip())
        return
    m = re.match(r"^grep\s+(\S+)(?:\s+(.+))?$", value, re.IGNORECASE)
    if m:
        cmd_grep(m.group(1), (m.group(2) or ".").strip())
        return
    m = re.match(r"^edit\s+(.+)$", value, re.IGNORECASE)
    if m:
        cmd_edit(m.group(1).strip())
        return
    m = re.match(r"^write\s+(.+)$", value, re.IGNORECASE)
    if m:
        cmd_write(m.group(1).strip())
        return
    if lower in {"todos", "todo", "plan", "make a plan"}:
        cmd_todos()
        return

    if lower.startswith("permission ") or lower.startswith("approve ") or lower.startswith("bash "):
        command = re.sub(r"^(permission|approve|bash)\s+", "", value, flags=re.IGNORECASE).strip() or "sleep 2 && echo ok"
        description = "Mock command requiring approval"
        n = print_bash_prompt(command, description)
        set_pending_permission(state, command, description, lines=n)
        return

    if (len(value) <= 60
            and re.search(r"\bdynamo\b", lower)
            and re.search(r"\brename\b", lower)):
        command, description, n = print_dynamo_rename_flow()
        set_pending_permission(state, command, description, lines=n)
        return

    m = re.match(r"^(sleep|wait)\s+(\d+(?:\.\d+)?)", value, re.IGNORECASE)
    if m:
        secs = m.group(2)
        unit = "second" if float(secs) == 1 else "seconds"
        description = f"Sleep for {secs} {unit}"
        n = print_bash_prompt(value, description)
        set_pending_permission(state, value, description, lines=n)
        return

    m = re.match(r"^(exec|execute)\s+(.+)$", value, re.IGNORECASE)
    if m:
        command = m.group(2).strip() or "echo ok"
        description = "Execute requested command (real shell)"
        n = print_bash_prompt(command, description)
        set_pending_permission(state, command, description, lines=n)
        state["real_exec"] = "1"
        return

    if len(value) <= 60 and re.search(r"\b(question|ask|choose)\b", lower):
        print_thinking()
        question = "Who is the greatest tennis player of all time?"
        options = ["Novak Djokovic", "Roger Federer", "Rafael Nadal"]
        print_question(question, options)
        set_pending_question(state, question, options)
        return

    if lower in {"guess", "guess number", "guess the number", "play guess", "number game", "play number"}:
        target = random.randint(0, 100)
        tries = 7
        set_pending_guess(state, target, tries)
        print(f"● I'm thinking of a number between 0 and 100. You have {tries} tries. What's your guess?")
        print()
        return

    if "20 questions" in lower:
        print_thinking()
        print_assistant("Think of something. I'll ask up to 20 yes/no questions to guess it. Tell me when you're ready.")
        print("● Q1/20: Is it a living thing?")
        print()
        return

    if lower in {"what can you do", "what do you do", "what can this do", "capabilities",
                 "what can you actually do", "what's possible", "what can i do",
                 "help me", "what now"}:
        print_capabilities()
        return

    if lower in {"hello", "hi", "hey", "yo"}:
        print_thinking(seconds=1, tokens=12)
        print_assistant(f"Hi. This is the {AGENT_PRODUCT_NAME} mock — try /help to see what I can do.")
        return

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
