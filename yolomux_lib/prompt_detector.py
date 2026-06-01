# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
from __future__ import annotations

"""Pure prompt detection, approval state, and command safety helpers."""

import hashlib
import re
import shlex

from . import yolo_rules


def is_dangerous(cmd_line: str) -> bool:
    """Return True when the shared YOLO rule engine identifies a dangerous command."""
    cmd_line = cmd_line.strip()
    if not cmd_line:
        return False

    if yolo_rules.hard_floor_decision(cmd_line):
        return True

    ruleset = yolo_rules.validate_rules(yolo_rules.default_rule_data("approve"), source="built-in")
    return yolo_rules.evaluate_ruleset(cmd_line, ruleset)["action"] == "block"


# ---------------------------------------------------------------------------
# Command extraction from pane text
# ---------------------------------------------------------------------------

# Lines to skip when collecting command text.
_SKIP_LINE = re.compile(
    r"^("
    r"─+$"
    r"|Bash command$"
    r"|Permission rule\b"
    r"|Do you want"
    r"|Running"
    r"|Esc to cancel"
    r")",
    re.IGNORECASE,
)

# Lines that look like commands (contain special shell chars, flags, or are long).
_CMD_CHARS = re.compile(r"[/|&;$=(>`~]|--|\s-[a-zA-Z]")


def _shell_text_complete(cmd_line: str) -> bool:
    try:
        shlex.split(cmd_line, posix=True)
        return True
    except ValueError:
        return False


def _codex_command_stop_line(line: str) -> bool:
    stripped = line.strip()
    return (
        bool(_YES_SELECTOR_RE.search(line))
        or bool(re.match(r"^[❯›]?\s*\d+\.\s+", stripped))
        or stripped.startswith("Press enter to confirm")
        or stripped.startswith("Press y to")
    )


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

    # Codex: command is on a "$ ..." line below the question.
    for i, line in enumerate(lines):
        if "Would you like to run the following command" in line:
            for j in range(i + 1, min(i + 12, len(lines))):
                stripped = lines[j].lstrip()
                # Codex prefixes the command with "$ " after leading box whitespace.
                if stripped.startswith("$ "):
                    cmd = stripped[2:].strip()
                    if not cmd or "<<" in cmd or _shell_text_complete(cmd):
                        return cmd or None
                    parts = [cmd]
                    for k in range(j + 1, min(j + 12, len(lines))):
                        if _codex_command_stop_line(lines[k]):
                            break
                        continuation = lines[k].strip()
                        if not continuation:
                            continue
                        parts.append(continuation)
                        joined = " ".join(parts)
                        if _shell_text_complete(joined):
                            return joined
                    return " ".join(parts)
                # Stop searching once we hit the selector; the command should
                # have appeared by then.
                if _YES_SELECTOR_RE.search(lines[j]):
                    break
            # No "$ " line found; fall through to the Claude path in case
            # this was a stale Codex header above a Claude prompt below.

    # Claude: walk backward from the trigger line to a separator/bullet.
    trigger_idx = None
    for i, line in enumerate(lines):
        if "Permission rule" in line or "Do you want to proceed" in line or "Do you want to make this edit" in line:
            trigger_idx = i
            break

    if trigger_idx is None:
        return None

    top_idx = 0
    found_content = False
    for i in range(trigger_idx - 1, -1, -1):
        stripped = lines[i].strip()

        if re.match(r"^─+$", stripped):
            top_idx = i + 1
            break

        if stripped.startswith("●"):
            top_idx = i + 1
            break

        if not stripped and found_content:
            top_idx = i + 1
            break

        if stripped:
            found_content = True

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


_FULL_PATH_RE = re.compile(
    r"(?:Write|Edit|Create|Read)\(([^)]+)\)"
    r"|(?:^|\s)(/\S+)"
    r"|(?:^|\s)((?:\w[\w.-]*/)+\w[\w.-]*)",
    re.MULTILINE,
)


def _find_full_path(pane_text: str, short_name: str) -> str:
    """Try to resolve a short filename to its full path from pane context."""
    if "/" in short_name:
        return short_name

    basename = short_name.rstrip("?").strip()

    for match in _FULL_PATH_RE.finditer(pane_text):
        path = match.group(1) or match.group(2) or match.group(3)
        if path and path.rstrip(")").endswith(basename):
            return path.rstrip(")")

    return short_name


# ---------------------------------------------------------------------------
# Prompt detection
# ---------------------------------------------------------------------------

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
    codex_body_until = -1
    for i, line in enumerate(pane_text.splitlines()):
        if "Do you want to proceed" in line:
            last_type = "bash"
        elif "Would you like to run the following command" in line:
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
_YES_SELECTOR_RE = re.compile(r"^\s*[❯›>]\s*1[.:]\s*\S", re.MULTILINE)


def _default_yes_choice_index(lines: list[str], prompt_index: int) -> int:
    yes_index = -1
    no_index = -1
    for index in range(prompt_index + 1, len(lines)):
        line = lines[index]
        if _SELECTED_CHOICE_LINE_RE.search(line):
            return -1
        if yes_index < 0 and _YES_OPTION_LINE_RE.search(line):
            yes_index = index
        if no_index < 0 and _NO_OPTION_LINE_RE.search(line):
            no_index = index
        if yes_index >= 0 and no_index >= 0:
            return yes_index if yes_index < no_index else -1
    return -1


def _approval_prompt_defaults_to_yes(pane_text: str) -> bool:
    lines = pane_text.splitlines()
    prompt_index = _last_approval_prompt_index(lines)
    return prompt_index >= 0 and _default_yes_choice_index(lines, prompt_index) >= 0


def yes_is_selected(pane_text: str) -> bool:
    """Check that the first option is currently highlighted.

    Works for both Claude (❯) and Codex (›) selectors, and for both Yes/No
    permission prompts and AskUserQuestion-style prompts with arbitrary
    first-option text.
    """
    if _YES_SELECTOR_RE.search(pane_text):
        return True
    return _approval_prompt_defaults_to_yes(pane_text) and not approval_prompt_has_later_activity(pane_text)


def selected_prompt_option(pane_text: str) -> int:
    matches = list(_SELECTED_CHOICE_NUMBER_RE.finditer(pane_text))
    if matches:
        return int(matches[-1].group(1))
    if _approval_prompt_defaults_to_yes(pane_text) and not approval_prompt_has_later_activity(pane_text):
        return 1
    return 0


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
_WORKING_LEADING_SYMBOL_RE = rf"(?:[{re.escape(_WORKING_SPINNER_GLYPHS)}]|[^\w\s])"
_WORKING_LINE_RE = re.compile(
    r"(?:"
    rf"^\s*{_WORKING_LEADING_SYMBOL_RE}\s+\S.*(?:…|\.{{3}}).*"
    r"(?:\([^)]*(?:thinking|tokens|effort|esc\s+to\s+interrupt|[↑↓]|\b\d+(?:\.\d+)?\s*[smh]\b)[^)]*\)|\b\d+(?:\.\d+)?\s*[smh]\b)"
    r"|^[^\n]*\([^)]*\besc\s+to\s+interrupt\b[^)]*\)"
    r")",
    re.IGNORECASE,
)
_CLAUDE_MULTI_AGENT_HEADER_RE = re.compile(
    rf"^\s*{_WORKING_LEADING_SYMBOL_RE}\s+Running\s+\d+\s+agents?\s*(?:…|\.{{3}})\s*$",
    re.IGNORECASE,
)
_CLAUDE_AGENT_TOKEN_SUBLINE_RE = re.compile(
    r"^\s*(?:[│├└]\s*)?\S.*\s·\s+(?:\d+\s+tool uses?\s+·\s+)?\d+(?:\.\d+)?[kKmM]?\s+tokens\b",
    re.IGNORECASE,
)
_BOX_DRAWING_ONLY_LINE_RE = re.compile(r"^[\s│┃╭╮╰╯┌┐└┘├┤┬┴┼─━═╔╗╚╝╠╣╦╩╬]+$")
_BOXED_EMPTY_INPUT_LINE_RE = re.compile(r"^\s*[│┃]\s*[❯›>]?\s*[█▉▊▋▌▍▎▏_ ]*[│┃]\s*$")
_EFFORT_STATUS_LINE_RE = re.compile(r"^\s*(?:[^\w\s]\s*)?\S+\s+/effort\b", re.IGNORECASE)
_WORK_QUEUE_HINT_RE = re.compile(r"(?:↑/↓\s+to\s+select|enter\s+to\s+view|↑\s+to\s+manage)", re.IGNORECASE)
_WORK_QUEUE_ROW_RE = re.compile(
    r"^\s*[○●◦]\s+\S.*(?:\b\d+(?:\.\d+)?\s*s\b|↑/↓\s+to\s+select|enter\s+to\s+view|↑\s+to\s+manage)",
    re.IGNORECASE,
)
_CHOICE_LINE_RE = re.compile(r"^\s*(?:[❯›>]\s*)?\d+[.:]\s+\S")
_SELECTED_CHOICE_LINE_RE = re.compile(r"^\s*[❯›>]\s*\d+[.:]\s+\S")
_SELECTED_CHOICE_NUMBER_RE = re.compile(r"^\s*[❯›>]\s*(\d+)[.:]\s+\S", re.MULTILINE)
_YES_OPTION_LINE_RE = re.compile(r"^\s*(?:[❯›>]\s*)?1[.:]\s+Yes\b", re.IGNORECASE)
_NO_OPTION_LINE_RE = re.compile(r"^\s*(?:[❯›>]\s*)?2[.:]\s+No\b", re.IGNORECASE)
_FOOTER_LINE_RE = re.compile(
    r"^\s*(?:\? for shortcuts|Esc to cancel|Enter to select|Tab to amend|ctrl\+e to explain)",
    re.IGNORECASE,
)
_FOOTER_HINT_SEPARATOR_RE = re.compile(r"\s*(?:[·•]|\.(?=\s))\s*")
_FOOTER_HINT_PART_RE = re.compile(
    r"^(?:"
    r"\? for shortcuts"
    r"|(?:esc|escape|enter|return|tab|shift\+tab|ctrl\+[a-z0-9]|cmd\+[a-z0-9]|alt\+[a-z0-9]|option\+[a-z0-9]|↑|↓|←|→)\s+to\s+.+"
    r")$",
    re.IGNORECASE,
)
_QUESTION_RE = re.compile(r"(?:^|\b)(?:Q\d+\s*/\s*\d+\s*:\s*)?.+\?\s*$")


def _is_working_line(line: str) -> bool:
    return bool(_WORKING_LINE_RE.search(line) or _CLAUDE_MULTI_AGENT_HEADER_RE.search(line))


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
        if _is_working_line(line):
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
        if _is_footer_hint_line(line):
            footer_index = index

    if selected_index < 0:
        selected_index = _default_yes_choice_index(lines, prompt_index)
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
    return (
        not stripped
        or _is_footer_hint_line(stripped)
        or bool(re.fullmatch(r"[─\-]{10,}", stripped))
        or bool(_BOX_DRAWING_ONLY_LINE_RE.fullmatch(stripped))
    )


def _is_footer_hint_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if _FOOTER_LINE_RE.search(stripped):
        return True
    if stripped.startswith("(") and stripped.endswith(")"):
        stripped = stripped[1:-1].strip()
    parts = [part.rstrip(".").strip() for part in _FOOTER_HINT_SEPARATOR_RE.split(stripped)]
    return bool(parts) and all(part and _FOOTER_HINT_PART_RE.match(part) for part in parts)


def _is_prompt_trailing_ui_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    if _is_footer_hint_line(stripped):
        return True
    if _is_working_line(line):
        return True
    if _BOX_DRAWING_ONLY_LINE_RE.fullmatch(stripped):
        return True
    if _BOXED_EMPTY_INPUT_LINE_RE.match(stripped):
        return True
    if _EFFORT_STATUS_LINE_RE.match(stripped):
        return True
    if _WORK_QUEUE_HINT_RE.search(stripped) or _WORK_QUEUE_ROW_RE.match(stripped):
        return True
    if _CLAUDE_AGENT_TOKEN_SUBLINE_RE.search(stripped):
        return True
    if re.match(r"^[⎿└]\s+Tip:", stripped, re.IGNORECASE):
        return True
    if re.match(r"^(?:gpt|claude|opus|sonnet)[A-Za-z0-9_.-]*\s+.+\s·\s", stripped, re.IGNORECASE):
        return True
    if re.match(r"^\d+\s+tasks\s+\(", stripped):
        return True
    if re.match(r"^\d+%\s+context\s+(?:used|left|remaining)\b", stripped, re.IGNORECASE):
        return True
    if stripped.startswith(("◼", "◻", "☑", "☒")):
        return True
    if re.fullmatch(r"[❯›>][\s█▉▊▋▌▍▎▏]*", stripped):
        return True
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

    prompt_line_visible = any(re.match(r"^\s*[❯›>]\s*$", line) for line in lines[-8:])
    if not prompt_line_visible:
        return ""
    for line in reversed(lines[:-1]):
        stripped = _clean_prompt_block_line(line)
        if not stripped or _is_footer_hint_line(stripped) or stripped.startswith(("keivenc@", "$ ")):
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
        if _WORKING_LINE_RE.search(stripped):
            continue
        normalized_lines.append(stripped)

    blob = "\n".join(normalized_lines).encode()
    return hashlib.md5(blob).hexdigest()


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
        "selected_option": selected_prompt_option(visible_text) if prompt_type is not None else 0,
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


__all__ = [
    "action_for_bash_prompt",
    "action_for_prompt",
    "agent_screen_state",
    "approval_prompt_has_later_activity",
    "approval_prompt_state",
    "detect_prompt",
    "extract_command",
    "is_dangerous",
    "prompt_hash",
    "prompt_text",
    "selected_prompt_option",
    "stale_approval_behind_working",
    "visible_agent_working",
    "visible_choice_prompt_text",
    "yes_is_selected",
]
