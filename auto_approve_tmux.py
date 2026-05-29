#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
from __future__ import annotations

"""Auto-approve Cursor CLI permission prompts in tmux sessions.

Watches one or more tmux panes for permission prompts and sends Enter to
approve them.  Blocks only genuinely dangerous commands (rm, rmdir, etc.).
Everything else is approved automatically.

Supports both Claude Code and Codex CLI prompts:

  Claude:                              Codex:
    Do you want to proceed?              Would you like to run the following command?
    Do you want to make this edit?        Would you like to make the following edits?
    ❯ 1. Yes                             › 1. Yes, proceed (y)
      2. No                                2. Yes, and don't ask again ... (p)
                                           3. No, and tell Codex ... (esc)

Detected prompt patterns:
  1. "Do you want to proceed?"                     (Claude bash)
  2. "Would you like to run the following command" (Codex bash)
  3. "Do you want to make this edit"               (Claude file edit)
  4. "Do you want to create"                       (Claude file create)
  5. "Would you like to make the following edits"  (Codex file edit)
  6. "Do you want to allow"                        (Claude tool, e.g. WebFetch)

Usage:
  ./auto_approve_tmux.py project1                  # single session
  ./auto_approve_tmux.py project1,project2          # comma-separated
  ./auto_approve_tmux.py "project*"                # wildcard (glob)
  ./auto_approve_tmux.py project1:0.1              # specific pane
  ./auto_approve_tmux.py --dry-run project1
  ./auto_approve_tmux.py --dry-run --once project1  # debug one prompt and exit
  ./auto_approve_tmux.py --once project1            # approve one prompt and exit
  ./auto_approve_tmux.py --list
"""

import argparse
import fnmatch
import hashlib
import logging
import os
import re
import signal
import subprocess
import sys
import time

log = logging.getLogger("auto_approve")

# If Enter is missed, a current prompt should not be stuck forever behind the
# de-dup hash. Retry only after the exact prompt remains visible briefly.
PROMPT_RETRY_SECONDS = 5.0

# ---------------------------------------------------------------------------
# Denylist: block only genuinely dangerous commands
# ---------------------------------------------------------------------------

DANGEROUS_COMMANDS = frozenset({
    "rm", "rmdir", "shred", "mkfs", "fdisk", "parted", "wipefs", "dd", "format",
})

DANGEROUS_PATTERNS = [
    # Redirection to block devices: `cmd > /dev/sd*`
    re.compile(r">\s*/dev/sd"),
    re.compile(r">\s*/dev/nvme"),
    re.compile(r">\s*/dev/vd"),
    # cp/mv/tee/cat writing *to* a block device as the destination path.
    # Catches: `cp image.iso /dev/sda`, `mv x /dev/nvme0n1`, `tee /dev/sda`, etc.
    # The token `/dev/sd*`, `/dev/nvme*`, `/dev/vd*` used as an argument is
    # almost always a wipe-the-disk footgun.
    re.compile(r"(?:^|\s)/dev/(?:sd[a-z]|nvme\d|vd[a-z])"),
    # find ... -delete  (recursive deletion via find)
    re.compile(r"\bfind\b[^|;&]*\s-delete\b"),
    # Fork bomb
    re.compile(r":\(\)\{.*:\|:&\};:"),
    # Redundant with _DANGER_WORDS_RE but kept as explicit documentation
    re.compile(r"sudo\s+rm\s"),
    re.compile(r"sudo\s+rmdir\s"),
]


# Characters that can legally precede a dangerous command token.
# Covers: start of string, whitespace, shell operators (; & | && ||),
# command substitution ($( `), and shell -c quoted wrappers (' ").
# Any of these means the following word is being *executed*, not used as data.
_DANGER_PREFIX = r"(?:^|[\s;&|`('\"$])"

# Build a single regex that matches any dangerous command as an executable
# token. We look at the *base name* by allowing an optional path prefix, and
# for mkfs/fdisk-style tools we allow .<suffix> (mkfs.ext4, mkfs.xfs, ...).
_DANGER_WORDS_RE = re.compile(
    _DANGER_PREFIX
    + r"(?:sudo\s+)?"                      # optional sudo wrapper
    + r"(?:/\S*/)?"                        # optional path prefix (/usr/bin/)
    + r"(?:" + "|".join(re.escape(c) for c in sorted(DANGEROUS_COMMANDS)) + r")"
    + r"(?:\.[A-Za-z0-9_-]+)?"             # optional .ext suffix (mkfs.ext4)
    + r"(?=[\s'\"`)]|$)"                   # followed by whitespace/quote/end
)


def is_dangerous(cmd_line: str) -> bool:
    """Return True if cmd_line contains a dangerous command, even when
    nested inside bash -c / sh -c / docker exec / ssh / quoted strings.

    Strategy: scan the whole command line for dangerous tokens preceded by
    a shell boundary (whitespace, operator, opening quote, etc.). This is a
    denylist that errs on the side of caution — if "rm" appears as an
    executable-looking token anywhere in the line, we block.

    Known false positives (acceptable): commands that have a filename
    literal like "rm" (e.g. `echo "run rm"`) will be blocked. In practice
    this is rare and safer than letting `bash -c 'rm -rf /'` slip through.
    """
    cmd_line = cmd_line.strip()
    if not cmd_line:
        return False

    for pat in DANGEROUS_PATTERNS:
        if pat.search(cmd_line):
            return True

    if _DANGER_WORDS_RE.search(cmd_line):
        return True

    return False


# ---------------------------------------------------------------------------
# Command extraction from tmux pane text
# ---------------------------------------------------------------------------

# Lines to skip when collecting command text
_SKIP_LINE = re.compile(
    r"^("
    r"─+$"                              # separator bars
    r"|Bash command$"                   # label
    r"|Permission rule\b"              # permission line
    r"|Do you want"                    # prompt line
    r"|Running"                        # status
    r"|Esc to cancel"                  # footer hint
    r")",
    re.IGNORECASE,
)

# Lines that look like commands (contain special shell chars, flags, or are long)
_CMD_CHARS = re.compile(r"[/|&;$=(>`~]|--|\s-[a-zA-Z]")


def extract_command(pane_text: str) -> str | None:
    """Extract the pending command from pane text around a permission prompt.

    Two layouts are supported:

    Claude:
        Bash command
            <command lines>           <- here
            <description>
        Permission rule Bash requires confirmation for this command.
        Do you want to proceed?
        ❯ 1. Yes

    Codex:
        Would you like to run the following command?
        Reason: <reason>
        $ <command line>              <- here
        › 1. Yes, proceed (y)

    For Claude we walk *backwards* from the trigger to the nearest separator.
    For Codex we look for the "$ " line *between* the question and the
    selector (it's below, not above).
    """
    lines = pane_text.splitlines()

    # --- Codex: command is on a "$ ..." line below the question.
    for i, line in enumerate(lines):
        if "Would you like to run the following command" in line:
            for j in range(i + 1, min(i + 12, len(lines))):
                stripped = lines[j].lstrip()
                # Codex prefixes the command with "$ " (after some leading
                # whitespace from the box layout).
                if stripped.startswith("$ "):
                    cmd = stripped[2:].strip()
                    return cmd or None
                # Stop searching once we hit the selector — the command
                # should have appeared by then.
                if _YES_SELECTOR_RE.search(lines[j]):
                    break
            # No "$ " line found; fall through to the Claude path in case
            # this was a stale Codex header above a Claude prompt below.

    # --- Claude: walk backward from the trigger line to a separator/bullet.
    trigger_idx = None
    for i, line in enumerate(lines):
        if "Permission rule" in line or "Do you want to proceed" in line or "Do you want to make this edit" in line:
            trigger_idx = i
            break

    if trigger_idx is None:
        return None

    # Walk backwards to find the top boundary
    top_idx = 0
    found_content = False
    for i in range(trigger_idx - 1, -1, -1):
        stripped = lines[i].strip()

        # Separator bar
        if re.match(r"^─+$", stripped):
            top_idx = i + 1
            break

        # Bullet line from previous tool output
        if stripped.startswith("●"):
            top_idx = i + 1
            break

        # Blank line after we've seen content — that's the boundary
        if not stripped and found_content:
            top_idx = i + 1
            break

        if stripped:
            found_content = True

    # Collect command lines from the window
    cmd_parts: list[str] = []
    for i in range(top_idx, trigger_idx):
        stripped = lines[i].strip()
        if not stripped:
            continue
        if _SKIP_LINE.match(stripped):
            continue
        if _CMD_CHARS.search(stripped) or len(stripped) > 60:
            cmd_parts.append(stripped)

    if not cmd_parts:
        return None

    return " ".join(cmd_parts)


# Patterns for finding full file paths in pane context
_FULL_PATH_RE = re.compile(
    r"(?:Write|Edit|Create|Read)\(([^)]+)\)"  # Write(/full/path/to/file)
    r"|(?:^|\s)(/\S+)"                        # or a bare absolute path
    r"|(?:^|\s)((?:\w[\w.-]*/)+\w[\w.-]*)",   # or a relative path with slashes
    re.MULTILINE,
)


def _find_full_path(pane_text: str, short_name: str) -> str:
    """Try to resolve a short filename to its full path from pane context.

    Looks for Write(path), Edit(path), or bare paths in the lines above the
    prompt that end with the same basename.
    """
    if "/" in short_name:
        return short_name

    basename = short_name.rstrip("?").strip()

    for m in _FULL_PATH_RE.finditer(pane_text):
        path = m.group(1) or m.group(2) or m.group(3)
        if path and path.rstrip(")").endswith(basename):
            return path.rstrip(")")

    return short_name


# ---------------------------------------------------------------------------
# Prompt detection
# ---------------------------------------------------------------------------

# Matches any file-related prompt: edit, create, overwrite, replace, etc.
_FILE_PROMPT_RE = re.compile(
    r"(?:"
    r"Do you want to (?:make this )?(?:edit|create|overwrite|replace|rename|move)\b[^?]*\?"
    r"|Would you like to make the following edits\?"
    r")",
    re.IGNORECASE,
)


def detect_prompt(pane_text: str) -> str | None:
    """Detect which kind of permission prompt is visible.

    Returns "bash" (Claude or Codex bash command), "file" (Claude file edit),
    "tool" (Claude tool, e.g. WebFetch), or None.

    Scans every line and keeps the LAST match so stale prompts that have
    scrolled up don't shadow the current one (the bottom-most match wins).

    Codex prompts contain a free-form "Reason:" line written by the model,
    and that prose can include phrases like "Do you want to allow ..." which
    would otherwise be mis-classified as a Claude tool prompt. To prevent
    that, once we see the Codex header we suppress tool-prompt detection
    inside the next ~20 lines (the Codex prompt body).
    """
    last_type = None
    codex_body_until = -1  # line index up to which we ignore "Do you want to allow"
    for i, line in enumerate(pane_text.splitlines()):
        if "Do you want to proceed" in line:
            last_type = "bash"
        elif "Would you like to run the following command" in line:
            # Codex bash command prompt — same intent as Claude's "proceed".
            last_type = "bash"
            codex_body_until = i + 20
        elif _FILE_PROMPT_RE.search(line):
            last_type = "file"
        elif "Do you want to allow" in line and i > codex_body_until:
            last_type = "tool"
    return last_type


# Selector glyphs for the highlighted option:
#   ❯  - Claude Code (U+276F HEAVY RIGHT-POINTING ANGLE QUOTATION MARK ORNAMENT)
#   ›  - Codex CLI   (U+203A SINGLE RIGHT-POINTING ANGLE QUOTATION MARK)
#
# Matches the first numbered option with the selector glyph. For Yes/No
# permission prompts the option text is literally "Yes"; for AskUserQuestion
# prompts it's arbitrary (e.g., "Go + gRPC"). We match any non-empty option
# text so the same check works for both shapes.
_YES_SELECTOR_RE = re.compile(r"[❯›]\s*1\.\s*\S")

def yes_is_selected(pane_text: str) -> bool:
    """Check that the first option is currently highlighted.

    Works for both Claude (❯) and Codex (›) selectors, and for both Yes/No
    permission prompts and AskUserQuestion-style prompts with arbitrary
    first-option text.
    """
    return bool(_YES_SELECTOR_RE.search(pane_text))


# Maps prompt type -> which option to select.
#   "option1": press Enter (select "Yes")
#   "option2": press Down + Enter (select "Yes, allow all ..." / "Yes, don't ask again ...")
#
# Rationale:
#   - bash:  option 1 (approve just this command; denylist handles dangerous ones)
#   - file:  option 2 ("Yes, allow all edits during this session") — avoid re-prompts
#   - tool:  option 2 ("Yes, and don't ask again for <domain>") — avoid re-prompts
PROMPT_ACTION = {
    "bash": "option1",
    "file": "option2",
    "tool": "option2",
}


def action_for_prompt(prompt_type: str | None) -> str | None:
    """Return which option to select for a given prompt type, or None to ignore."""
    if prompt_type is None:
        return None
    return PROMPT_ACTION.get(prompt_type)


_CODEX_OPTION2_PREFIX_RE = re.compile(
    r"^\s*2\.\s+Yes, and don't ask again for commands that start with `([^`]+)`",
    re.MULTILINE,
)
_CODEX_GENERIC_OPTION2_PREFIXES = frozenset({
    "gh api",
})


def action_for_bash_prompt(pane_text: str) -> str:
    """Return the option to pick for a bash prompt.

    Claude bash prompts only have "Yes" / "No", so option 1 is the default.
    Codex has an option 2 for "don't ask again for commands that start with X".
    That is useful only when X is generic enough to recur (for now: ``gh api``).
    Exact command prefixes with PR numbers are intentionally left at option 1.
    """
    match = _CODEX_OPTION2_PREFIX_RE.search(pane_text)
    if match and match.group(1).strip() in _CODEX_GENERIC_OPTION2_PREFIXES:
        return "option2"
    return "option1"


def prompt_text(pane_text: str, prompt_type: str | None = None) -> str:
    """Return the bottom-most visible approval prompt text.

    This is the display companion to ``detect_prompt``. Keep the matching
    rules aligned so UI code and auto-approval code describe the same prompt.
    """
    text = ""
    codex_body_until = -1
    lines = pane_text.splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if "Do you want to proceed" in stripped:
            text = "Do you want to proceed?"
        elif "Would you like to run the following command" in stripped:
            text = "Would you like to run the following command?"
            codex_body_until = i + 20
        else:
            file_match = _FILE_PROMPT_RE.search(stripped)
            if file_match:
                text = stripped
            elif "Do you want to allow" in stripped and i > codex_body_until:
                text = stripped
    if text:
        return text
    if prompt_type == "bash":
        return "approval prompt is visible"
    if prompt_type == "file":
        return "file approval prompt is visible"
    if prompt_type == "tool":
        return "tool approval prompt is visible"
    return ""


_WORKING_SPINNER_GLYPHS = "✢✣✤✥✦✧✩✱✲✳✴✵✶✷✸✹✺✻✽✾✿*+·•◦∙⋅"
_WORKING_LINE_RE = re.compile(
    r"(?:"
    rf"[{re.escape(_WORKING_SPINNER_GLYPHS)}]\s+.+…\s*\("
    r"[^)]*(?:thinking|tokens|effort|esc\s+to\s+interrupt|↓|\b\d+(?:\.\d+)?\s*[smh]\b)[^)]*\)"
    r"|[^\n]*\([^)]*\besc\s+to\s+interrupt\b[^)]*\)"
    r")",
    re.IGNORECASE,
)
_CHOICE_LINE_RE = re.compile(r"^\s*(?:[❯›>]\s*)?\d+[.:]\s+\S")
_SELECTED_CHOICE_LINE_RE = re.compile(r"^\s*[❯›>]\s*\d+[.:]\s+\S")
_FOOTER_LINE_RE = re.compile(
    r"^\s*(?:\? for shortcuts|Esc to cancel|Enter to select|Tab to amend|ctrl\+e to explain)",
    re.IGNORECASE,
)
_QUESTION_RE = re.compile(r"(?:^|\b)(?:Q\d+\s*/\s*\d+\s*:\s*)?.+\?\s*$")


def visible_agent_working(visible_text: str) -> bool:
    """Return True when the visible pane is showing an active thinking/working row."""
    lines = visible_text.splitlines()[-25:]
    working_index = _last_working_index(lines)
    return working_index >= 0 and not _working_line_has_later_prompt(lines, working_index)


def _working_line_has_later_prompt(lines: list[str], working_index: int) -> bool:
    for line in lines[working_index + 1:]:
        stripped = line.strip()
        if not stripped or _is_separator_or_footer(line) or _is_prompt_trailing_ui_line(line):
            continue
        if re.match(r"^[❯›>]\s+\S", stripped):
            continue
        if re.search(r"[@:][^@\s]+[$#]\s*$", stripped):
            return True
        return True
    return False


def _last_approval_prompt_index(lines: list[str]) -> int:
    last_index = -1
    codex_body_until = -1
    for index, line in enumerate(lines):
        if "Do you want to proceed" in line or "Would you like to run the following command" in line:
            last_index = index
            if "Would you like to run the following command" in line:
                codex_body_until = index + 20
        elif _FILE_PROMPT_RE.search(line):
            last_index = index
        elif "Do you want to allow" in line and index > codex_body_until:
            last_index = index
    return last_index


def _last_working_index(lines: list[str]) -> int:
    last_index = -1
    for index, line in enumerate(lines):
        if _WORKING_LINE_RE.search(line):
            last_index = index
    return last_index


def stale_approval_behind_working(visible_text: str) -> bool:
    """Return True when old prompt text remains visible above a live working row."""
    lines = visible_text.splitlines()
    prompt_index = _last_approval_prompt_index(lines)
    return (
        prompt_index >= 0
        and _last_working_index(lines) > prompt_index
        and approval_prompt_has_later_activity(visible_text)
    )


def approval_prompt_has_later_activity(visible_text: str) -> bool:
    """Return True when a dismissed prompt remains visible above newer output."""
    lines = visible_text.splitlines()
    prompt_index = _last_approval_prompt_index(lines)
    if prompt_index < 0:
        return False

    footer_index = -1
    selected_index = -1
    for index in range(prompt_index + 1, len(lines)):
        line = lines[index]
        if _SELECTED_CHOICE_LINE_RE.search(line):
            selected_index = index
        if _FOOTER_LINE_RE.search(line.strip()):
            footer_index = index

    if selected_index < 0:
        return False

    start = footer_index + 1 if footer_index >= 0 else selected_index + 1
    for line in lines[start:]:
        stripped = line.strip()
        if (
            _is_separator_or_footer(line)
            or _CHOICE_LINE_RE.search(line)
            or _is_prompt_trailing_ui_line(line)
        ):
            continue
        if footer_index >= 0:
            return True
        if stripped.startswith(("●", "•", "✻", "✢", "❯", "›")):
            return True
        if re.search(r"[@:][^@\s]+[$#]\s*$", stripped):
            return True
    return False


def _clean_prompt_block_line(line: str) -> str:
    return re.sub(r"\s+", " ", line.strip().lstrip("●•").lstrip("❯›>").strip())


def _is_separator_or_footer(line: str) -> bool:
    stripped = line.strip()
    return not stripped or bool(_FOOTER_LINE_RE.search(stripped)) or bool(re.fullmatch(r"[─\-]{10,}", stripped))


def _is_prompt_trailing_ui_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    if _WORKING_LINE_RE.search(line):
        return True
    if re.match(r"^[⎿└]\s+Tip:", stripped, re.IGNORECASE):
        return True
    if re.match(r"^(?:gpt|claude|opus|sonnet)[A-Za-z0-9_.-]*\s+.+\s·\s", stripped, re.IGNORECASE):
        return True
    if re.match(r"^\d+\s+tasks\s+\(", stripped):
        return True
    if stripped.startswith(("◼", "◻", "☑", "☒")):
        return True
    # Empty user-input prompt: bare "›" / "❯" / ">" with no command yet.
    if re.fullmatch(r"[❯›>][\s█▉▊▋▌▍▎▏]*", stripped):
        return True
    # Claude footer/status: "▶▶ bypass permissions on · N shell · esc to interrupt · ↓ to ..."
    if re.search(r"\b(?:bypass\s+permissions|esc\s+to\s+interrupt|\d+\s+shells?\b)", stripped, re.IGNORECASE):
        return True
    return False


def _clip_prompt_lines(lines: list[str], max_lines: int = 12, max_chars: int = 1200) -> str:
    cleaned = [_clean_prompt_block_line(line) for line in lines]
    cleaned = [line for line in cleaned if line and not re.fullmatch(r"got it\.?", line, re.IGNORECASE)]
    text = "\n".join(cleaned[:max_lines])
    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    return text


def visible_choice_prompt_text(visible_text: str) -> str:
    """Return the current user-question prompt from the visible pane only.

    This intentionally ignores scrollback. If a spinner/working line is visible,
    working wins over older questions still present above the prompt.
    """
    if not visible_text.strip():
        return ""
    if detect_prompt(visible_text) is not None or visible_agent_working(visible_text):
        return ""
    if re.search(r"accept edits on\s*\(", visible_text, re.IGNORECASE):
        return ""

    lines = visible_text.splitlines()[-80:]
    selected_indices = [i for i, line in enumerate(lines) if _SELECTED_CHOICE_LINE_RE.search(line)]
    if selected_indices:
        selected = selected_indices[-1]
        start = selected
        while start > 0 and not _is_separator_or_footer(lines[start - 1]):
            start -= 1
        question_index = start - 1
        while question_index >= 0 and not lines[question_index].strip():
            question_index -= 1
        if question_index >= 0 and _QUESTION_RE.match(_clean_prompt_block_line(lines[question_index])):
            start = question_index
        end = selected + 1
        while end < len(lines) and not _is_separator_or_footer(lines[end]):
            end += 1
        block = lines[start:end]
        if sum(1 for line in block if _CHOICE_LINE_RE.search(line)) >= 2:
            return _clip_prompt_lines(block)

    # Plain assistant question with an empty input prompt below it.
    prompt_line_visible = any(re.match(r"^\s*[❯›>]\s*$", line) for line in lines[-8:])
    if not prompt_line_visible:
        return ""
    for line in reversed(lines[:-1]):
        stripped = _clean_prompt_block_line(line)
        if not stripped or _FOOTER_LINE_RE.search(stripped) or stripped.startswith(("keivenc@", "$ ")):
            continue
        if _QUESTION_RE.match(stripped):
            return stripped
        if re.match(r"^\s*[❯›>]\s+\S", line):
            break
    return ""


def agent_screen_state(visible_text: str) -> dict[str, object]:
    """Classify the visible terminal screen for YOLOmux UI badges.

    Approval detection and auto-approval use the same visible pane text as this
    UI state, so the browser does not need its own stale scrollback parser.
    """
    prompt_state = approval_prompt_state(visible_text)
    prompt_type = prompt_state.get("type") or None
    if prompt_type is not None:
        return {
            "key": "approval",
            "text": str(prompt_state.get("text") or prompt_text(visible_text, prompt_type)),
            "prompt_type": prompt_type,
        }
    if visible_agent_working(visible_text):
        return {"key": "working", "text": "agent is working"}
    question = visible_choice_prompt_text(visible_text)
    if question:
        return {"key": "needs-input", "text": question}
    return {"key": "idle", "text": ""}


def approval_prompt_state(visible_text: str, pane_text: str | None = None) -> dict[str, object]:
    """Return structured approval prompt state from the shared detector.

    ``visible_text`` must be captured with ``visible_only=True``. ``pane_text``
    may include scrollback and is used only to extract command context after a
    visible bash prompt has already been confirmed.
    """
    prompt_type = detect_prompt(visible_text)
    selected = yes_is_selected(visible_text)
    if prompt_type is not None and (
        stale_approval_behind_working(visible_text) or approval_prompt_has_later_activity(visible_text)
    ):
        prompt_type = None

    state: dict[str, object] = {
        "visible": prompt_type is not None,
        "type": prompt_type or "",
        "text": prompt_text(visible_text, prompt_type) if prompt_type is not None else "",
        "yes_selected": selected if prompt_type is not None else False,
        "action": None,
        "command": None,
        "dangerous": False,
        "hash": prompt_hash(visible_text) if prompt_type is not None else "",
    }
    if prompt_type is None:
        return state
    action = action_for_bash_prompt(visible_text) if prompt_type == "bash" else action_for_prompt(prompt_type)
    state["action"] = action or ""
    if prompt_type == "bash" and pane_text is not None:
        command = extract_command(pane_text)
        state["command"] = command
        state["dangerous"] = command is not None and is_dangerous(command)
    return state


# ---------------------------------------------------------------------------
# tmux helpers
# ---------------------------------------------------------------------------

def tmux_run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["tmux", *args],
        capture_output=True, text=True, check=check,
    )


def tmux_list_sessions() -> str | None:
    result = tmux_run("list-sessions", check=False)
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def tmux_session_names() -> list[str]:
    """Return list of all tmux session names."""
    result = tmux_run("list-sessions", "-F", "#{session_name}", check=False)
    if result.returncode != 0:
        return []
    return [s.strip() for s in result.stdout.splitlines() if s.strip()]


def tmux_has_session(session: str) -> bool:
    return session in tmux_session_names()


def tmux_exact_target_from_sessions(target: str, sessions: list[str]) -> str:
    """Return a tmux target that cannot confuse a numeric session with a window.

    `tmux -t 1` can mean window 1 in the current session, not the session named
    `1`. When the requested target exactly matches a session name, use `1:` so
    tmux resolves it as that session's active pane.
    """
    if not target or target.startswith("%"):
        return target
    if target in sessions:
        return f"{target}:"
    return target


def tmux_exact_target(target: str) -> str:
    return tmux_exact_target_from_sessions(target, tmux_session_names())


def tmux_capture_pane(target: str, lines: int = 80, visible_only: bool = False) -> str | None:
    """Capture the contents of a tmux pane.

    When ``visible_only`` is True, capture ONLY the current visible screen
    (no scrollback history). This is critical for detecting active prompts:
    a dismissed prompt that has scrolled up still lives in scrollback and
    would otherwise trick ``detect_prompt`` into thinking the prompt is
    still on screen, causing the retry loop to fire on a ghost prompt.

    Use visible_only=True for presence detection (detect_prompt, yes_is_selected,
    prompt_hash). Use the default scrollback capture only when you need
    context above the prompt (extract_command, _find_full_path).
    """
    exact_target = tmux_exact_target(target)
    if visible_only:
        result = tmux_run("capture-pane", "-t", exact_target, "-p", check=False)
    else:
        result = tmux_run("capture-pane", "-t", exact_target, "-p", "-S", f"-{lines}", check=False)
    if result.returncode != 0:
        return None
    return result.stdout


def tmux_send_enter(target: str) -> None:
    tmux_run("send-keys", "-t", tmux_exact_target(target), "Enter", check=False)


def tmux_send_option2(target: str) -> None:
    """Select option 2 by pressing Down then Enter."""
    exact_target = tmux_exact_target(target)
    tmux_run("send-keys", "-t", exact_target, "Down")
    time.sleep(0.3)
    tmux_send_enter(exact_target)


# ---------------------------------------------------------------------------
# Target resolution (multiple args, comma-separated, wildcards)
# ---------------------------------------------------------------------------

def _iter_target_parts(specs: list[str]) -> list[str]:
    """Flatten argv target specs into individual target parts."""
    parts: list[str] = []
    for spec in specs:
        for part in spec.split(","):
            part = part.strip()
            if part:
                parts.append(part)
    return parts


def specs_have_wildcards(specs: list[str]) -> bool:
    """Return True if any target spec includes a wildcard session name."""
    for part in _iter_target_parts(specs):
        session_part = part.split(":")[0]
        if any(c in session_part for c in "*?[]"):
            return True
    return False


def _resolve_targets_from_sessions(specs: list[str], all_sessions: list[str]) -> list[str]:
    """Expand target specs into concrete tmux targets using known sessions."""
    targets: list[str] = []
    seen: set[str] = set()

    for part in _iter_target_parts(specs):
        session_part = part.split(":")[0]

        if any(c in session_part for c in "*?[]"):
            for name in all_sessions:
                if fnmatch.fnmatch(name, session_part):
                    suffix = part[len(session_part):]
                    expanded = name + suffix
                    if expanded not in seen:
                        targets.append(expanded)
                        seen.add(expanded)
        else:
            if part not in seen:
                targets.append(part)
                seen.add(part)

    return targets


def resolve_targets(specs: list[str]) -> list[str]:
    """Expand target specs into a list of tmux targets."""
    return _resolve_targets_from_sessions(specs, tmux_session_names())


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def prompt_hash(pane_text: str) -> str:
    """Hash the visible approval prompt block to deduplicate repeated polls.

    Claude Yes/No prompts all have the same selector text. Include the command
    block above the selector so two different commands do not look like the
    same already-approved prompt.
    """
    all_lines = pane_text.splitlines()
    selector_re = re.compile(r"[❯›]\s*\d+\.\s*\S")
    selector_index = -1
    for i, line in enumerate(all_lines):
        if selector_re.search(line):
            selector_index = i

    if selector_index >= 0:
        start = max(0, selector_index - 80)
        for i in range(selector_index - 1, -1, -1):
            stripped = all_lines[i].strip()
            if re.fullmatch(r"[─\-]{10,}", stripped):
                start = i + 1
                break
            if stripped.startswith(("● Bash(", "• Bash(")):
                start = i
                break
            if stripped in {"Bash command", "Bash command (unsandboxed)"}:
                start = i
        end = min(len(all_lines), selector_index + 6)
        context_lines = all_lines[start:end]
    else:
        context_lines = all_lines[-80:]

    normalized_lines: list[str] = []
    for line in context_lines:
        stripped = line.rstrip()
        if not stripped:
            continue
        # Spinner elapsed time is not part of the approval identity.
        if _WORKING_LINE_RE.search(stripped):
            continue
        normalized_lines.append(stripped)

    blob = "\n".join(normalized_lines).encode()
    return hashlib.md5(blob).hexdigest()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Auto-approve Cursor CLI permission prompts in tmux sessions.",
        epilog=(
            "Target formats:\n"
            '  project1                single session\n'
            '  project1 project2        multiple positional targets\n'
            '  project1,project2        comma-separated\n'
            '  project1 "project*"      mixed explicit + wildcard\n'
            '  "project*"             wildcard (glob against session names)\n'
            '  project1:0.1            specific window.pane\n'
            "\n"
            "To create/attach a named tmux session:\n"
            "  tmux new-session -s mysession      # create new\n"
            "  tmux attach -t mysession           # reattach to existing\n"
            "  tmux new-session -A -s mysession   # attach if exists, else create"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("targets", nargs="*", default=[],
                        help='tmux target(s): separate args, "s1,s2", or "pattern*"')
    parser.add_argument("--dry-run", action="store_true",
                        help="show what would be approved without sending keys")
    parser.add_argument("--verbose", action="store_true",
                        help="print every poll cycle")
    parser.add_argument("--interval", type=float, default=0.5,
                        help="base poll interval in seconds (default: 0.5)")
    parser.add_argument("--list", action="store_true",
                        help="list available tmux sessions and exit")
    parser.add_argument("--once", action="store_true",
                        help="process one visible prompt, then exit (useful with --dry-run)")
    parser.add_argument("--self-test", action="store_true",
                        help="run built-in tests and exit")
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def _self_test() -> bool:
    """Run built-in tests using real prompt outputs from live sessions.

    Returns True if all tests pass.
    """
    failed = 0
    total = 0

    def check(label: str, actual: object, expected: object) -> None:
        nonlocal failed, total
        total += 1
        if actual != expected:
            failed += 1
            print(f"  FAIL  {label}")
            print(f"        expected {expected!r}, got {actual!r}")
        else:
            print(f"  OK    {label}")

    # =====================================================================
    # is_dangerous — denylist
    # =====================================================================
    print("=== is_dangerous: BLOCKED commands ===")
    dangerous_cmds = [
        "rm -rf /tmp/foo",
        "rm file.txt",
        "rm -f somefile.txt",
        "sudo rm -rf /",
        "sudo rm -r /var/log/old",
        "rmdir /some/dir",
        "sudo rmdir /foo",
        "dd if=/dev/zero of=/dev/sda",
        "dd if=image.iso of=/dev/nvme0n1 bs=4M",
        "mkfs.ext4 /dev/sda1",
        "mkfs.xfs /dev/nvme0n1p1",
        "shred /dev/sda",
        "shred -vfz -n 5 /dev/sdb",
        "fdisk /dev/sda",
        "parted /dev/sda print",
        "wipefs -a /dev/sda",
        "format C:",
        "echo foo > /dev/sda",
        "cat /dev/urandom > /dev/nvme0n1",
        "FOO=bar rm -rf /",
        "CUDA_VISIBLE_DEVICES=0 rm -rf /workspace",
        "ls /tmp && rm -rf /important",
        "echo done; sudo rm -rf /",
        # Nested inside shell -c / docker exec / ssh — MUST NOT slip through
        "bash -c 'rm -rf /tmp/foo'",
        "sh -c 'rm -rf /workspace'",
        'bash -c "rm -rf /tmp"',
        "bash -lc 'rm -rf /tmp'",
        'docker exec abc bash -c "rm -rf /workspace"',
        'docker exec abc sh -c "rm -rf /"',
        "docker exec abc rm -rf /workspace",
        "ssh user@host 'rm -rf /tmp/stuff'",
        "FOO=bar bash -c 'rm -rf /tmp'",
        'docker exec abc bash -c "cd /tmp && rm -rf build"',
        # Path-prefixed dangerous commands
        "/bin/rm -rf /tmp/foo",
        "/usr/bin/rm file",
        # Command substitution wrappers
        "echo $(rm -rf /tmp)",
        "echo `rm -rf /tmp`",
        # find ... -delete
        "find /tmp -name '*.log' -delete",
        'ssh host "find /tmp -delete"',
        # cp/mv/tee writing to block devices (not just `>` redirection)
        "cp image.iso /dev/sda",
        "mv foo /dev/nvme0n1",
        "echo x | tee /dev/sda",
        # xargs rm / find -exec rm — recursive deletion wrappers
        'find /tmp -name "*.log" | xargs rm -f',
        'find /tmp -name "*.tmp" -exec rm {} \\;',
        # kubectl exec / docker run with dangerous payload
        "kubectl exec pod -- rm -rf /workspace",
        "docker run ubuntu rm -rf /",
    ]
    for cmd in dangerous_cmds:
        check(f"BLOCK  {cmd}", is_dangerous(cmd), True)

    print()
    print("=== is_dangerous: ALLOWED commands ===")
    safe_cmds = [
        # Real: simple file ops
        "mv file1 file2",
        "cp file1 file2",
        "\\mv -f ~/.claude/skills/dyn-pull-build-localdev-all ~/.claude/skills/dyn-pull-and-build-localdev-all",
        "chmod +x script.sh",
        "chmod +x ~/.claude/skills/dyn-pull-and-build-localdev-all/pick_images.py",
        # Real: read-only / inspection
        "ls -la",
        "ls ~/project/.claude/skills/review-pr/ 2>/dev/null",
        "ls ~/project/ 2>/dev/null; echo '---'; find ~/project* -name 'gh_review.sh' -type f 2>/dev/null | head -5",
        "ls -la ~/project1 2>&1 | head -5",
        "cat /etc/passwd",
        "cat /tmp/review.json",
        "head -n 20 somefile.txt",
        "tail -5 /tmp/output.log",
        "tail -20 /tmp/build-localdev-vllm.log",
        "grep -r pattern src/",
        "find . -name '*.py'",
        "find ~/project -maxdepth 5 -name 'gh_review.sh' -type f 2>/dev/null",
        "stat -c '%s bytes, modified %y' /tmp/build-localdev-vllm.log",
        "wc -l /tmp/pr8269.diff",
        # Real: curl / network
        "curl https://example.com",
        "curl -s localhost:8081/metrics > /tmp/trtllm-backend-metrics.txt",
        'curl -sL http://speedoflight.nv.com/project/commits/index.json | python3 -c "import json, sys; data = json.load(sys.stdin)"',
        # Real: docker exec (most common pattern)
        'docker exec foo bash -c "ls"',
        'docker exec fa12f1aa09df bash -c "python3 /utils/soak_fe.py --max-tokens 1000 --requests_per_worker 5"',
        'docker exec fa12f1aa09df bash -c "curl -s http://localhost:8000/v1/models 2>&1 | head -c 500"',
        'docker exec da18aeee0059 bash -c "cd /workspace && CUDA_VISIBLE_DEVICES=0 WORKSPACE_DIR=/workspace python3 -m pytest tests/serve/test_vllm.py -v --timeout=300 2>&1"',
        'docker exec 1c5b911efcb7 bash -c "grep \'_TEST_META_FILENAME\' /workspace/tests/utils/vram_utils.py"',
        'docker exec fa12f1aa09df bash -c "~/utils/await_output.sh -t 240 -s \'model_name=Qwen\' -q -- tail -n +1 -F /home/project/notes/inference.log 2>&1 | tail -5"',
        # Real: docker status / images / cleanup (no rm)
        "docker images projectci.azurecr.io/ai-project/project --format '{{.Tag}} {{.Size}}' 2>/dev/null | grep 261881221",
        'docker ps -a --format "{{.ID}}\\t{{.Names}}\\t{{.Image}}\\t{{.Status}}" | grep vsc-project',
        "docker manifest inspect projectci.azurecr.io/ai-project/project:abc-vllm-dev-cuda13 >/dev/null 2>&1",
        "docker pull projectci.azurecr.io/ai-project/project:abc-vllm-dev-cuda13",
        "docker rmi projectci.azurecr.io/ai-project/project:old-tag",
        "docker system df 2>&1 | head -10",
        # Real: git / gh
        "git push origin main",
        "gh pr view 8362 --repo ai-project/project --json title,body,author 2>&1",
        "gh pr diff 8362 --repo ai-project/project 2>&1 | head -600",
        "gh pr diff 8362 --repo ai-project/project 2>&1 | sed -n '600,900p'",
        "gh pr checks 1234 --repo ai-project/project",
        # Real: python / pytest / build
        "python3 -m pytest tests/",
        "python3 ~/.claude/skills/dyn-pull-and-build-localdev-all/pick_images.py",
        "cargo fmt",
        "npm install",
        # Real: gh_review.sh
        "~/.claude/skills/dyn-review-pr/gh_review.sh reviews ai-project/project 8362 2>&1 | head -100",
        "cat /tmp/review.json | ~/.claude/skills/dyn-review-pr/gh_review.sh post ai-project/project 8362 2>&1",
        # Real: GH API via curl + token
        'GH_TOKEN=$(grep oauth_token ~/.config/gh/hosts.yml | head -1 | awk \'{print $2}\') && curl -sH "Authorization: token $GH_TOKEN" "https://api.github.com/repos/ai-project/project/contents/tests/hf_cache.py?ref=main"',
        # Real: multi-command status checks
        'echo "=== pulls running ==="; pgrep -af "docker pull" 2>/dev/null | grep -v pgrep || echo "(none)"',
        'pgrep -af "docker build.*local-dev" 2>/dev/null | grep -v pgrep || echo "(none)"',
        "journalctl -u docker --since '10 min ago' --no-pager 2>/dev/null | tail -5",
        'nohup bash /tmp/bench_ecr_vs_acr.sh > /tmp/bench_ecr_vs_acr.log 2>&1 &',
        # Real: process inspection
        'ps -eo pid,ppid,cmd --no-headers | grep -E "python|project|vllm" | head -30',
        "ss -tlnp 2>/dev/null | grep -E '8000|8081'",
        "nv-smi --query-gpu=index,memory.used,memory.total --format=csv,noheader",
        # Edge: empty / whitespace
        "",
        "   ",
    ]
    for cmd in safe_cmds:
        check(f"ALLOW  {cmd}", is_dangerous(cmd), False)

    # =====================================================================
    # detect_prompt — real pane outputs
    # =====================================================================
    print()
    print("=== detect_prompt: real pane outputs ===")

    # Real: bash command with docker exec
    check("bash: docker exec pytest",
          detect_prompt(
              "● Bash(docker exec da18aeee0059 bash -c \"cd /workspace && python3 -m pytest tests/ -v\")\n"
              "\n"
              "─────────────────────────────────────────────\n"
              " Bash command\n"
              "\n"
              "   docker exec da18aeee0059 bash -c \"cd /workspace && python3 -m pytest tests/ -v\"\n"
              "   Run tests\n"
              "\n"
              " Permission rule Bash requires confirmation for this command.\n"
              "\n"
              " Do you want to proceed?\n"
              " ❯ 1. Yes\n"
              "   2. No\n"
              "\n"
              " Esc to cancel · Tab to amend · ctrl+e to explain\n"
          ),
          "bash")

    # Real: bash command (unsandboxed) with tail
    check("bash: unsandboxed tail",
          detect_prompt(
              "  3 tasks (2 done, 1 in progress, 0 open)\n"
              "  ✔ Bump timeouts for L4 machines\n"
              "  ✔ Run serial test to verify VRAM stays capped\n"
              "  ◼ Run parallel test to confirm all gpu_1 pass\n"
              "\n"
              "─────────────────────────────────────────────\n"
              " Bash command (unsandboxed)\n"
              "\n"
              "   tail -5 /tmp/claude-1776734304/tasks/bh3cuwyx3.output\n"
              "   Check parallel test progress\n"
              "\n"
              " Permission rule Bash requires confirmation for this command.\n"
              "\n"
              " Do you want to proceed?\n"
              " ❯ 1. Yes\n"
              "   2. No\n"
              "\n"
              " Esc to cancel · Tab to amend · ctrl+e to explain\n"
          ),
          "bash")

    # Real: bash with curl + metrics
    check("bash: curl metrics pipeline",
          detect_prompt(
              "─────────────────────────────────────────────\n"
              " Bash command\n"
              "\n"
              "   curl -s localhost:8081/metrics > /tmp/trtllm-backend-metrics.txt && "
              "curl -s localhost:8000/metrics > /tmp/trtllm-frontend-metrics.txt && "
              "wc -l /tmp/trtllm-backend-metrics.txt /tmp/trtllm-frontend-metrics.txt\n"
              "   Collect TRT-LLM metrics from both ports\n"
              "\n"
              " Permission rule Bash requires confirmation for this command.\n"
              "\n"
              " Do you want to proceed?\n"
              " ❯ 1. Yes\n"
              "   2. No\n"
          ),
          "bash")

    # Real: bash with ls | grep
    check("bash: ls pipe grep",
          detect_prompt(
              "─────────────────────────────────────────────\n"
              " Bash command\n"
              "\n"
              "   ls ~/.claude/skills/ | grep -i localdev\n"
              "   Check for existing localdev skill\n"
              "\n"
              " Permission rule Bash requires confirmation for this command.\n"
              "\n"
              " Do you want to proceed?\n"
              " ❯ 1. Yes\n"
              "   2. No\n"
              "\n"
              " Esc to cancel · Tab to amend · ctrl+e to explain\n"
          ),
          "bash")

    # Real: file edit with SKILL.md rename
    check("file: edit SKILL.md name field",
          detect_prompt(
              "─────────────────────────────────────────────\n"
              " Edit file\n"
              " .claude/skills/dyn-commit/SKILL.md\n"
              "╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌\n"
              " 1  ---\n"
              " 2 -name: commit\n"
              " 2 +name: dyn-commit\n"
              " 3  description: Create a git commit\n"
              "╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌\n"
              " Do you want to make this edit to SKILL.md?\n"
              " ❯ 1. Yes\n"
              "   2. Yes, and allow Claude to edit its own settings for this session\n"
              "   3. No\n"
          ),
          "file")

    # Real: file edit with .cursorrules
    check("file: edit .cursorrules",
          detect_prompt(
              " Do you want to make this edit to .cursorrules?\n"
              " ❯ 1. Yes\n"
              "   2. Yes, allow all edits during this session (shift+tab)\n"
              "   3. No\n"
          ),
          "file")

    # Real: file create
    check("file: create SKILL.md",
          detect_prompt(
              " Do you want to create SKILL.md?\n"
              " ❯ 1. Yes\n"
              "   2. Yes, allow all edits during this session (shift+tab)\n"
              "   3. No\n"
          ),
          "file")

    # Real: file create with full path context
    check("file: create pick_images.py",
          detect_prompt(
              "● Update(.claude/skills/dyn-pull-build-localdev-all/pick_images.py)\n"
              "\n"
              " Do you want to create pick_images.py?\n"
              " ❯ 1. Yes\n"
              "   2. Yes, allow all edits during this session\n"
              "   3. No\n"
          ),
          "file")

    # Real: file overwrite
    check("file: overwrite pick_images.py",
          detect_prompt(
              " Do you want to overwrite pick_images.py?\n"
              " ❯ 1. Yes\n"
              "   2. Yes, allow all edits during this session\n"
              "   3. No\n"
          ),
          "file")

    # Real: Codex file edit prompt
    codex_edit_pane = (
        "Would you like to make the following edits?\n"
        "\n"
        "› 1. Yes, proceed (y)\n"
        "  2. Yes, and don't ask again for these files (a)\n"
        "  3. No, and tell Codex what to do differently (esc)\n"
        "\n"
        "  Press enter to confirm or esc to cancel\n"
    )
    check("codex file: Would you like to make edits", detect_prompt(codex_edit_pane), "file")
    check("codex file: › selector recognized", yes_is_selected(codex_edit_pane), True)

    # Real: Codex bash prompt — "Would you like to run the following command?"
    codex_pane = (
        "◦ Running gh api repos/ai-project/project/pulls/9579/comments\n"
        "\n"
        "  Would you like to run the following command?\n"
        "\n"
        "  Reason: Do you want to allow GitHub network access so I can fetch PR #9579 status?\n"
        "\n"
        "  $ ~/ai-config/claude/bin/dyn_gh_ops.py pr-status --pr 9579\n"
        "\n"
        "› 1. Yes, proceed (y)\n"
        "  2. Yes, and don't ask again for commands that start with `'~/ai-config/claude/bin/dyn_gh_ops.py' pr-status --pr 9579` (p)\n"
        "  3. No, and tell Codex what to do differently (esc)\n"
        "\n"
        "  Press enter to confirm or esc to cancel\n"
    )
    check("codex bash: Would you like to run", detect_prompt(codex_pane), "bash")
    check("codex bash: › selector recognized", yes_is_selected(codex_pane), True)

    # Codex: simpler one without leading bullet
    codex_simple = (
        "  Would you like to run the following command?\n"
        "\n"
        "  $ git status\n"
        "\n"
        "› 1. Yes, proceed (y)\n"
        "  2. No\n"
    )
    check("codex bash: simple git status", detect_prompt(codex_simple), "bash")

    # Codex: › selector but no Yes -> not selected (defensive)
    check("codex: no selected hides yes",
          yes_is_selected("  1. Yes, proceed (y)\n› 2. No\n"), False)

    # Real: WebFetch tool prompt
    check("tool: WebFetch raw.githubusercontent.com",
          detect_prompt(
              "─────────────────────────────────────────────\n"
              " Fetch\n"
              "\n"
              "   https://raw.githubusercontent.com/sgl-project/sglang/v0.5.9/python/sglang/srt/managers/tokenizer_manager.py\n"
              "   Claude wants to fetch content from raw.githubusercontent.com\n"
              "\n"
              " Permission rule WebFetch requires confirmation for this tool.\n"
              "\n"
              " Do you want to allow Claude to fetch this content?\n"
              " ❯ 1. Yes\n"
              "   2. Yes, and don't ask again for raw.githubusercontent.com\n"
              "   3. No, and tell Claude what to do differently (esc)\n"
          ),
          "tool")

    # Real: delete should NOT auto-approve
    check("file: delete NOT matched",
          detect_prompt(
              " Do you want to delete old_script.sh?\n"
              " ❯ 1. Yes\n"
              "   2. No\n"
          ),
          None)

    # Real: no prompt — just Claude working
    check("no prompt: Claude thinking",
          detect_prompt(
              "✶ Drafting replies to review comments… (19m 6s · ↓ 2.3k tokens)\n"
              "  ⎿  ✔ Fix bare URL in README.md\n"
              "     ✔ Refactor LoRA methods into LoraMixin\n"
              "     ◼ Draft replies to review comments\n"
              "\n"
              "─────────────────────────────────────────────\n"
              "❯ \n"
              "─────────────────────────────────────────────\n"
              "  esc to interrupt · ctrl+t to hide tasks\n"
          ),
          None)

    # Real: no prompt — shell prompt
    check("no prompt: shell prompt",
          detect_prompt(
              "keivenc@keivenc-linux:~/project/project3$ gg\n"
              "=== Git log ===\n"
              "c78fac3 style: cargo fmt on test_streaming_tool_parsers.rs\n"
              "keivenc@keivenc-linux:~/project/project3$\n"
          ),
          None)

    # Stale prompt above, current prompt below (bottom wins)
    check("stale bash above, current file below (bottom wins)",
          detect_prompt(
              " Do you want to proceed?\n"
              " ❯ 1. Yes\n"
              "   2. No\n"
              "\n"
              "● Some more work\n"
              "\n"
              " Do you want to make this edit to SKILL.md?\n"
              " ❯ 1. Yes\n"
              "   2. Yes, and allow Claude to edit its own settings\n"
              "   3. No\n"
          ),
          "file")

    check("stale file above, current bash below (bottom wins)",
          detect_prompt(
              " Do you want to create bar.py?\n"
              " ❯ 1. Yes\n"
              "\n"
              "● Running commands\n"
              "\n"
              " Do you want to proceed?\n"
              " ❯ 1. Yes\n"
              "   2. No\n"
          ),
          "bash")

    check("stale bash above, current tool below (bottom wins)",
          detect_prompt(
              " Do you want to proceed?\n"
              " ❯ 1. Yes\n"
              "\n"
              "● Fetching...\n"
              "\n"
              " Do you want to allow Claude to fetch this content?\n"
              " ❯ 1. Yes\n"
              "   2. Yes, and don't ask again\n"
              "   3. No\n"
          ),
          "tool")

    # =====================================================================
    # action_for_prompt — which option we pick per prompt type
    # =====================================================================
    print()
    print("=== action_for_prompt: which option to pick ===")

    check("bash -> option1 (Yes)",
          action_for_prompt("bash"), "option1")
    check("file -> option2 (Yes, allow all edits)",
          action_for_prompt("file"), "option2")
    check("tool -> option2 (Yes, don't ask again for domain)",
          action_for_prompt("tool"), "option2")
    check("None -> None (no action)",
          action_for_prompt(None), None)
    check("unknown -> None (safety fallback)",
          action_for_prompt("unknown"), None)
    check("codex bash exact PR prefix -> option1",
          action_for_bash_prompt(
              "  Would you like to run the following command?\n"
              "  $ ~/ai-config/claude/bin/dyn_gh_ops.py pr-status --pr 9579\n"
              "› 1. Yes, proceed (y)\n"
              "  2. Yes, and don't ask again for commands that start with "
              "`'~/ai-config/claude/bin/dyn_gh_ops.py' pr-status --pr 9579` (p)\n"
          ),
          "option1")
    check("codex bash generic gh api prefix -> option2",
          action_for_bash_prompt(
              "  Would you like to run the following command?\n"
              "  $ gh api repos/ai-project/project/pulls/9579/comments\n"
              "› 1. Yes, proceed (y)\n"
              "  2. Yes, and don't ask again for commands that start with `gh api` (p)\n"
          ),
          "option2")
    check("claude bash no option2 prefix -> option1",
          action_for_bash_prompt(" Do you want to proceed?\n ❯ 1. Yes\n   2. No\n"),
          "option1")

    # =====================================================================
    # yes_is_selected
    # =====================================================================
    print()
    print("=== yes_is_selected ===")

    check("yes selected (2-option)", yes_is_selected(
        " ❯ 1. Yes\n   2. No\n"), True)
    check("yes selected (3-option edit)", yes_is_selected(
        " ❯ 1. Yes\n   2. Yes, allow all edits during this session\n   3. No\n"), True)
    check("yes selected (3-option tool)", yes_is_selected(
        " ❯ 1. Yes\n   2. Yes, and don't ask again for raw.githubusercontent.com\n   3. No\n"), True)
    check("no selected", yes_is_selected(
        "   1. Yes\n ❯ 2. No\n"), False)
    check("option 2 selected (edit)", yes_is_selected(
        "   1. Yes\n ❯ 2. Yes, allow all edits during this session\n   3. No\n"), False)
    check("no selector at all", yes_is_selected("random text"), False)

    # =====================================================================
    # prompt_hash
    # =====================================================================
    print()
    print("=== prompt_hash: deduplicates only identical visible prompts ===")

    yes_no_prompt_a = """Bash command (unsandboxed)

   echo one
   Pause before continuing

 Permission rule Bash requires confirmation for this command.

 Do you want to proceed?
 ❯ 1. Yes
   2. No

 Esc to cancel · Tab to amend · ctrl+e to explain
"""
    yes_no_prompt_b = yes_no_prompt_a.replace("echo one", "echo two")
    check("same command prompt hashes match",
          prompt_hash(yes_no_prompt_a), prompt_hash(yes_no_prompt_a))
    check("different command prompts hash differently",
          prompt_hash(yes_no_prompt_a) != prompt_hash(yes_no_prompt_b), True)
    check("current visible approval prompt is active",
          approval_prompt_state(yes_no_prompt_a)["visible"], True)
    prompt_with_tasks = yes_no_prompt_a + (
        "\n  4 tasks (0 done, 1 in progress, 3 open)\n"
        "  ◼ Fix parser output leak\n"
        "  ◻ Update fixtures\n"
    )
    check("current approval prompt with task list below is active",
          approval_prompt_state(prompt_with_tasks)["visible"], True)
    prompt_with_status = yes_no_prompt_a + "\n✢ Metamorphosing… (21m 2s · ↓ 295 tokens · thought for 2s)\n"
    check("current approval prompt with status row below is active",
          approval_prompt_state(prompt_with_status)["visible"], True)
    dismissed_prompt = yes_no_prompt_a + "\n● User approved Claude's request\n● Bash(echo one)\n  ⎿  ok\n\n❯ \n"
    check("dismissed approval prompt above newer output is ignored",
          approval_prompt_state(dismissed_prompt)["visible"], False)

    # =====================================================================
    # extract_command — real pane layouts
    # =====================================================================
    print()
    print("=== extract_command: real pane layouts ===")

    # Real: docker exec with pytest
    cmd = extract_command(
        "● Some context\n"
        "\n"
        "─────────────────────────────────────────────\n"
        " Bash command\n"
        "\n"
        '   docker exec da18aeee0059 bash -c "cd /workspace && CUDA_VISIBLE_DEVICES=0 '
        "python3 -m pytest tests/serve/test_vllm.py -v --timeout=300 2>&1\"\n"
        "   Run gpu_1 vllm tests in parallel\n"
        "\n"
        " Permission rule Bash requires confirmation for this command.\n"
        "\n"
        " Do you want to proceed?\n"
        " ❯ 1. Yes\n"
    )
    check("extracts docker exec pytest", cmd is not None and "docker exec" in cmd and "pytest" in cmd, True)

    # Real: curl metrics pipeline
    cmd = extract_command(
        "● Collecting metrics\n"
        "\n"
        "─────────────────────────────────────────────\n"
        " Bash command\n"
        "\n"
        "   curl -s localhost:8081/metrics > /tmp/backend.txt && curl -s localhost:8000/metrics > /tmp/frontend.txt\n"
        "   Collect metrics from both ports\n"
        "\n"
        " Permission rule Bash requires confirmation for this command.\n"
        "\n"
        " Do you want to proceed?\n"
    )
    check("extracts curl metrics", cmd is not None and "curl" in cmd and "metrics" in cmd, True)

    # Real: gh pr view
    cmd = extract_command(
        "─────────────────────────────────────────────\n"
        " Bash command\n"
        "\n"
        "   gh pr view 8362 --repo ai-project/project --json title,body,author 2>&1\n"
        "   View PR details\n"
        "\n"
        " Permission rule Bash requires confirmation for this command.\n"
        "\n"
        " Do you want to proceed?\n"
    )
    check("extracts gh pr view", cmd is not None and "gh pr view" in cmd, True)

    # Real: gh_review.sh
    cmd = extract_command(
        "─────────────────────────────────────────────\n"
        " Bash command\n"
        "\n"
        "   ~/.claude/skills/dyn-review-pr/gh_review.sh reviews ai-project/project 8362 2>&1 | head -80\n"
        "   List existing reviews on PR 8362\n"
        "\n"
        " Permission rule Bash requires confirmation for this command.\n"
        "\n"
        " Do you want to proceed?\n"
    )
    check("extracts gh_review.sh", cmd is not None and "gh_review.sh" in cmd, True)

    # Real: unsandboxed tail
    cmd = extract_command(
        "─────────────────────────────────────────────\n"
        " Bash command (unsandboxed)\n"
        "\n"
        "   tail -10 /tmp/claude-1776734304/tasks/bh3cuwyx3.output\n"
        "   Check parallel test progress\n"
        "\n"
        " Permission rule Bash requires confirmation for this command.\n"
        "\n"
        " Do you want to proceed?\n"
    )
    check("extracts unsandboxed tail", cmd is not None and "tail" in cmd, True)

    # Real: multi-line docker exec with heredoc
    cmd = extract_command(
        "─────────────────────────────────────────────\n"
        " Bash command\n"
        "\n"
        '   DIR=/home/project/notes/logs; docker exec fa12f1aa09df bash -c "set -x; '
        "curl -sS -N --max-time 2 -o $DIR/h2a.body.sse -D $DIR/h2a.headers "
        "-H 'Content-Type: application/json' -d @/tmp/h2a-body.json "
        'http://localhost:8000/v1/chat/completions 2>&1"\n'
        "   H2a: curl streaming chat completions\n"
        "\n"
        " Permission rule Bash requires confirmation for this command.\n"
        "\n"
        " Do you want to proceed?\n"
    )
    check("extracts multi-line docker exec curl",
          cmd is not None and "docker exec" in cmd and "curl" in cmd, True)

    # Codex: command on "$ " line below the question
    cmd = extract_command(
        "  Would you like to run the following command?\n"
        "\n"
        "  Reason: Do you want to allow GitHub network access so I can fetch PR #9579 status?\n"
        "\n"
        "  $ ~/ai-config/claude/bin/dyn_gh_ops.py pr-status --pr 9579\n"
        "\n"
        "› 1. Yes, proceed (y)\n"
    )
    check("codex: extracts dyn_gh_ops.py pr-status",
          cmd, "~/ai-config/claude/bin/dyn_gh_ops.py pr-status --pr 9579")

    cmd = extract_command(
        "  Would you like to run the following command?\n"
        "\n"
        "  $ git status\n"
        "\n"
        "› 1. Yes, proceed (y)\n"
    )
    check("codex: extracts plain git status", cmd, "git status")

    cmd = extract_command(
        "  Would you like to run the following command?\n"
        "\n"
        "  $ docker exec foo bash -c \"ls -la /workspace\"\n"
        "\n"
        "› 1. Yes, proceed (y)\n"
    )
    check("codex: extracts docker exec",
          cmd, 'docker exec foo bash -c "ls -la /workspace"')

    # No prompt at all
    check("returns None with no prompt", extract_command("just text\nno prompt\n"), None)

    # =====================================================================
    # target resolution
    # =====================================================================
    print()
    print("=== target resolution ===")

    sessions = ["project1", "project2", "project3", "misc"]
    check("single target",
          _resolve_targets_from_sessions(["project1"], sessions),
          ["project1"])
    check("multiple positional targets",
          _resolve_targets_from_sessions(["project1", "project2"], sessions),
          ["project1", "project2"])
    check("comma-separated targets",
          _resolve_targets_from_sessions(["project1,project2"], sessions),
          ["project1", "project2"])
    check("mixed positional + comma-separated",
          _resolve_targets_from_sessions(["project1", "project2,project3"], sessions),
          ["project1", "project2", "project3"])
    check("wildcard target",
          _resolve_targets_from_sessions(["project*"], sessions),
          ["project1", "project2", "project3"])
    check("mixed explicit + wildcard dedups exact duplicate",
          _resolve_targets_from_sessions(["project1", "project*"], sessions),
          ["project1", "project2", "project3"])
    check("wildcard preserves pane suffix",
          _resolve_targets_from_sessions(["project*:0.1"], sessions),
          ["project1:0.1", "project2:0.1", "project3:0.1"])
    check("same session with two panes kept separately",
          _resolve_targets_from_sessions(["project1:0.0", "project1:1.0"], sessions),
          ["project1:0.0", "project1:1.0"])
    check("wildcard detection false",
          specs_have_wildcards(["project1", "project2:0.1"]), False)
    check("wildcard detection true",
          specs_have_wildcards(["project1", "dyn*"]), True)
    numeric_sessions = ["1", "6", "ant"]
    check("numeric session target becomes explicit session",
          tmux_exact_target_from_sessions("1", numeric_sessions), "1:")
    check("named session target becomes explicit session",
          tmux_exact_target_from_sessions("ant", numeric_sessions), "ant:")
    check("pane id target stays unchanged",
          tmux_exact_target_from_sessions("%79", numeric_sessions), "%79")
    check("explicit pane target stays unchanged",
          tmux_exact_target_from_sessions("1:0.0", numeric_sessions), "1:0.0")

    # =====================================================================
    # Summary
    # =====================================================================
    print()
    if failed:
        print(f"FAILED: {failed}/{total} tests")
        return False
    print(f"ALL {total} TESTS PASSED")
    return True


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

class SessionState:
    """Per-session tracking for dedup and counters."""

    def __init__(self, target: str) -> None:
        self.target = target
        self.label = target.split(":")[0]  # short name for log output
        self.last_hash = ""
        self.last_hash_at = 0.0
        self.last_blocked_hash = ""
        self.approved = 0
        self.blocked = 0


def main() -> None:
    args = parse_args()

    logging.basicConfig(
        format="[%(asctime)s] %(message)s",
        datefmt="%H:%M:%S",
        level=logging.DEBUG if args.verbose else logging.INFO,
    )

    if args.self_test:
        sys.exit(0 if _self_test() else 1)

    if args.list:
        sessions = tmux_list_sessions()
        print(sessions or "No tmux sessions found.")
        sys.exit(0)

    if not args.targets:
        print("No tmux target specified. Available sessions:")
        print()
        sessions = tmux_list_sessions()
        print(sessions or "  (none)")
        print()
        print('Usage: auto_approve_tmux.py <target> [<target> ...]  (e.g. project1 project2 "project*" d1,d2)')
        print()
        print("To create/attach a named tmux session:")
        print("  tmux new-session -s mysession      # create new")
        print("  tmux attach -t mysession           # reattach to existing")
        print("  tmux new-session -A -s mysession   # attach if exists, else create")
        sys.exit(1)

    # Check if the spec uses wildcards — if so, we'll re-resolve each cycle
    target_display = " ".join(args.targets)
    is_dynamic = specs_have_wildcards(args.targets)

    # Initial resolution
    targets = resolve_targets(args.targets)
    if not targets:
        if is_dynamic:
            log.info("No sessions match '%s' yet — waiting for them to appear...", target_display)
        else:
            print(f"Error: no tmux sessions match '{target_display}'.")
            print("Available sessions:")
            sessions = tmux_list_sessions()
            print(sessions or "  (none)")
            sys.exit(1)

    # For non-dynamic specs, verify sessions exist upfront
    if not is_dynamic:
        bad: list[str] = []
        for t in targets:
            session_name = t.split(":")[0]
            if not tmux_has_session(session_name):
                bad.append(session_name)
        if bad:
            print(f"Error: tmux session(s) not found: {', '.join(bad)}")
            print("Available sessions:")
            sessions = tmux_list_sessions()
            print(sessions or "  (none)")
            sys.exit(1)

    states: dict[str, SessionState] = {t: SessionState(t) for t in targets}

    def refresh_targets() -> None:
        """Re-resolve dynamic targets, adding new sessions and removing gone ones."""
        current_targets = resolve_targets(args.targets)
        current_set = set(current_targets)
        existing_set = set(states.keys())

        for t in current_set - existing_set:
            states[t] = SessionState(t)
            log.info("New session detected: %s", states[t].label)

        for t in existing_set - current_set:
            st = states.pop(t)
            log.info("Session gone: %s (was %d approved, %d blocked)", st.label, st.approved, st.blocked)

    # Dedup for log output (across all sessions)
    last_log_msg = ""
    repeat_count = 0

    def log_dedup(level: int, msg: str) -> None:
        """Log a message, collapsing consecutive identical lines into '......'."""
        nonlocal last_log_msg, repeat_count
        if msg == last_log_msg:
            repeat_count += 1
            if repeat_count == 1:
                log.log(level, "......")
            return
        if repeat_count > 1:
            log.log(level, "...... (%d repeats)", repeat_count)
        last_log_msg = msg
        repeat_count = 0
        log.log(level, "%s", msg)

    def on_exit(signum: int, _frame: object) -> None:
        sig_name = signal.Signals(signum).name
        parts = [f"{s.label}: {s.approved} approved, {s.blocked} blocked" for s in states.values()]
        log.info("Caught %s — exiting. %s", sig_name, " | ".join(parts))
        sys.exit(0)

    signal.signal(signal.SIGINT, on_exit)
    signal.signal(signal.SIGTERM, on_exit)

    def should_exit_once(st: SessionState, outcome: str) -> bool:
        """Return True when --once has processed its first prompt."""
        if not args.once:
            return False
        log.info("ONCE complete after %s on %s (%d approved, %d blocked)",
                 outcome, st.label, st.approved, st.blocked)
        return True

    # Adaptive polling: lerp from base to max over ramp_duration seconds of inactivity
    base_interval = args.interval
    max_interval = max(2.5, base_interval)
    ramp_duration = 60.0  # seconds of inactivity before reaching max_interval
    idle_since: float | None = None  # monotonic timestamp when idle streak started

    sys.stderr.write("=" * 72 + "\n")
    sys.stderr.write(" AUTO-APPROVE TMUX\n")
    sys.stderr.write("\n")
    sys.stderr.write(" WARNING: This script auto-approves agent CLI prompts (Claude + Codex):\n")
    sys.stderr.write("   - Bash prompts  -> option 1 (Yes / Yes, proceed)\n")
    sys.stderr.write("   - File edits    -> option 2 (Yes, allow all edits this session)\n")
    sys.stderr.write("   - Tool prompts  -> option 2 (Yes, don't ask again for domain)\n")
    sys.stderr.write("\n")
    sys.stderr.write(" Bash commands are approved EXCEPT when they match the denylist\n")
    sys.stderr.write(" (detection is nesting-aware: catches bash -c, docker exec, ssh,\n")
    sys.stderr.write("  xargs, find -exec, kubectl exec, etc.):\n")
    sys.stderr.write("   - rm, rmdir, shred, mkfs, fdisk, parted, wipefs, dd, format\n")
    sys.stderr.write("   - sudo rm/rmdir\n")
    sys.stderr.write("   - find ... -delete\n")
    sys.stderr.write("   - any reference to /dev/sd*, /dev/nvme*, /dev/vd*\n")
    sys.stderr.write("   - fork bombs (:(){ :|:& };:)\n")
    sys.stderr.write("\n")
    sys.stderr.write(" File 'delete' prompts are NOT auto-approved (manual confirmation required).\n")
    sys.stderr.write("\n")
    sys.stderr.write(" USE AT YOUR OWN RISK. Review the denylist before relying on this.\n")
    sys.stderr.write(" The author is not responsible for unintended side effects.\n")
    sys.stderr.write("=" * 72 + "\n")
    sys.stderr.write("\n")
    sys.stderr.flush()

    if states:
        target_names = ", ".join(s.label for s in states.values())
        log.info("Watching %d session(s): %s", len(states), target_names)
    if is_dynamic:
        log.info("Dynamic mode: will auto-detect new sessions matching '%s'", target_display)
    log.info("Poll interval: %ss (ramps to %ss over %ds idle)", base_interval, max_interval, int(ramp_duration))
    if args.dry_run:
        log.info("DRY RUN — will not send keys")
    if args.once:
        log.info("ONCE — will exit after processing the first visible prompt")
    log.info("Press Ctrl+C to stop")
    print()

    while True:
        if is_dynamic:
            refresh_targets()

        acted = False

        for st in list(states.values()):
            # Detection uses the VISIBLE pane only. Using scrollback here
            # causes false-positive retries because a dismissed prompt that
            # scrolled up still contains "Do you want to proceed" text.
            visible_text = tmux_capture_pane(st.target, visible_only=True)
            if visible_text is None:
                log.warning("[%s] Failed to capture pane. Session still alive?", st.label)
                continue

            prompt_type = detect_prompt(visible_text)

            if prompt_type is None:
                st.last_hash = ""  # reset so next prompt is always fresh
                st.last_hash_at = 0.0
                st.last_blocked_hash = ""
                log_dedup(logging.DEBUG, f"[{st.label}] No prompt (approved={st.approved} blocked={st.blocked})")
                continue

            if not yes_is_selected(visible_text):
                log_dedup(logging.DEBUG, f"[{st.label}] Prompt found but 'Yes' not selected")
                continue

            # Prompt is genuinely on screen — now grab the scrollback capture
            # to get enough context for command extraction / full-path lookup.
            pane_text = tmux_capture_pane(st.target)
            if pane_text is None:
                pane_text = visible_text

            current_hash = prompt_hash(visible_text)
            now = time.monotonic()
            if current_hash == st.last_blocked_hash:
                log_dedup(logging.DEBUG, f"[{st.label}] Blocked prompt still visible; waiting for manual action")
                continue
            if current_hash == st.last_hash and now - st.last_hash_at < PROMPT_RETRY_SECONDS:
                log_dedup(logging.DEBUG, f"[{st.label}] Approved prompt still visible; waiting before retry")
                continue
            if current_hash == st.last_hash:
                log_dedup(logging.INFO, f"[{st.label}] Approved prompt still visible after {PROMPT_RETRY_SECONDS:g}s; retrying")

            acted = True

            # Dispatch based on prompt type -> option mapping. Bash defaults
            # to option 1, except for Codex prompts whose option-2 prefix is
            # generic enough to be useful across future commands (e.g. gh api).
            if prompt_type == "bash":
                action = action_for_bash_prompt(visible_text)
            else:
                action = action_for_prompt(prompt_type)

            def _send(opt: str) -> None:
                if args.dry_run:
                    return
                if opt == "option2":
                    tmux_send_option2(st.target)
                else:
                    tmux_send_enter(st.target)

            if prompt_type == "file":
                # "Do you want to [make this] <verb> [to] <filename>?"
                match = re.search(
                    r"Do you want to (?:make this )?(\w+)\s+(?:to\s+)?([^?\n]+)\?",
                    pane_text, re.IGNORECASE,
                )
                verb = match.group(1).strip() if match else "file"
                short_name = match.group(2).strip() if match else "(file)"
                desc = _find_full_path(pane_text, short_name)
                opt_label = "opt 2" if action == "option2" else "opt 1"
                verb_word = "WOULD APPROVE" if args.dry_run else "APPROVE"
                log.info("[%s] %s (%s, %s): %s", st.label, verb_word, verb, opt_label, desc)
                _send(action)
                st.last_hash = current_hash
                st.last_hash_at = time.monotonic()
                st.last_blocked_hash = ""
                st.approved += 1
                if should_exit_once(st, "file approval"):
                    return
                time.sleep(3)

            elif prompt_type == "tool":
                # "Permission rule <Tool> requires confirmation" / "Do you want to allow Claude to <action>?"
                match = re.search(r"Permission rule (\w+) requires confirmation", pane_text)
                tool_name = match.group(1) if match else "tool"
                opt_label = "opt 2" if action == "option2" else "opt 1"
                verb_word = "WOULD APPROVE" if args.dry_run else "APPROVE"
                log.info("[%s] %s (tool, %s): %s", st.label, verb_word, opt_label, tool_name)
                _send(action)
                st.last_hash = current_hash
                st.last_hash_at = time.monotonic()
                st.last_blocked_hash = ""
                st.approved += 1
                if should_exit_once(st, "tool approval"):
                    return
                time.sleep(3)

            else:  # bash prompt
                cmd = extract_command(pane_text)

                if cmd is None:
                    opt_label = "opt 2" if action == "option2" else "opt 1"
                    if last_log_msg != f"[{st.label}] APPROVE (no cmd extracted, defaulting yes)":
                        pane_lines = pane_text.splitlines()
                        for i, line in enumerate(pane_lines):
                            if "Do you want to proceed" in line or "Would you like to run the following command" in line:
                                start = max(0, i - 10)
                                context = pane_lines[start : i + 1]
                                log.warning("[%s] Could not extract command. Context:", st.label)
                                for ctx in context:
                                    print(f"  | {ctx}")
                                break
                    if args.dry_run:
                        log_dedup(logging.INFO, f"[{st.label}] WOULD APPROVE (no cmd extracted, {opt_label})")
                    else:
                        log_dedup(logging.INFO, f"[{st.label}] APPROVE (no cmd extracted, {opt_label})")
                        _send(action)
                    st.last_hash = current_hash
                    st.last_hash_at = time.monotonic()
                    st.last_blocked_hash = ""
                    st.approved += 1
                    if should_exit_once(st, "bash approval without command extraction"):
                        return
                    time.sleep(3)

                elif is_dangerous(cmd):
                    log.info("[%s] BLOCKED (dangerous): %s", st.label, cmd)
                    st.last_hash = current_hash
                    st.last_hash_at = time.monotonic()
                    st.last_blocked_hash = current_hash
                    st.blocked += 1
                    if should_exit_once(st, "dangerous-command block"):
                        return

                else:
                    opt_label = "opt 2" if action == "option2" else "opt 1"
                    if args.dry_run:
                        log.info("[%s] WOULD APPROVE (%s): %s", st.label, opt_label, cmd)
                    else:
                        log.info("[%s] APPROVE (%s): %s", st.label, opt_label, cmd)
                        _send(action)
                    st.last_hash = current_hash
                    st.last_hash_at = time.monotonic()
                    st.last_blocked_hash = ""
                    st.approved += 1
                    if should_exit_once(st, "bash approval"):
                        return
                    time.sleep(3)

        if acted:
            idle_since = None
            time.sleep(base_interval)
        else:
            now = time.monotonic()
            if idle_since is None:
                idle_since = now
            idle_secs = now - idle_since
            t = min(idle_secs / ramp_duration, 1.0)  # 0..1 over ramp_duration
            current_interval = base_interval + t * (max_interval - base_interval)
            time.sleep(current_interval)


if __name__ == "__main__":
    main()
