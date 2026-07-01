# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Pure prompt detection, approval state, and command safety helpers.

Keep the detector contract and raw prompt examples synced with
docs/specs/AGENT_PROMPTS_AND_COMMUNICATION.md.
"""

from __future__ import annotations

import hashlib
import re
import shlex
import time

from . import yolo_rules

_ANSI_ESCAPE_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_COMMON_VISUAL_WRAP_COLS = frozenset({78, 80, 100, 120, 126, 150, 200})
_VISUAL_WRAP_CONTINUATION_RE = re.compile(r"^\s+|^[a-z0-9_./~:+#?)\]}-]", re.IGNORECASE)
_BOX_OR_RULE_RE = re.compile(r"^[\s─━╌╍▔╭╮╰╯│┌┐└┘├┤┬┴┼═║╔╗╚╝╠╣╦╩╬\-]+$")


def _visual_wrap_candidate_cols(lines: list[str]) -> set[int]:
    counts: dict[int, int] = {}
    for line in lines:
        length = len(line.rstrip())
        if length >= 60:
            counts[length] = counts.get(length, 0) + 1
    inferred = {length for length, count in counts.items() if count >= 2}
    return set(_COMMON_VISUAL_WRAP_COLS) | inferred


def _is_visual_wrap_continuation(previous: str, current: str, wrap_cols: set[int]) -> bool:
    prev = previous.rstrip()
    cur = current.rstrip()
    stripped = cur.strip()
    if not stripped:
        return False
    prev_len = len(prev)
    if not any(prev_len in {cols - 1, cols} for cols in wrap_cols):
        return False
    if re.search(r"(?:…|\.{3}).*\([^)]*(?:tokens|thinking|effort|esc\s+to\s+interrupt)[^)]*\)", prev, re.IGNORECASE):
        return False
    if _BOX_OR_RULE_RE.fullmatch(prev.strip()) or _BOX_OR_RULE_RE.fullmatch(stripped):
        return False
    if re.match(r"^[❯›>]?\s*\d+[.:]\s+\S", stripped):
        return False
    if re.match(r"^[❯›>]\s+\S", stripped):
        return False
    return bool(_VISUAL_WRAP_CONTINUATION_RE.match(cur))


def _join_visual_wraps(text: str) -> str:
    lines = str(text or "").splitlines()
    if not lines:
        return ""
    wrap_cols = _visual_wrap_candidate_cols(lines)
    joined: list[str] = []
    previous_physical = ""
    for line in lines:
        if joined and _is_visual_wrap_continuation(previous_physical, line, wrap_cols):
            if line.startswith((" ", "\t")):
                joined[-1] = joined[-1].rstrip() + " " + line.lstrip()
            elif line.startswith("+"):
                joined[-1] = joined[-1].rstrip() + " " + line.lstrip()
            else:
                joined[-1] = joined[-1].rstrip() + line.lstrip()
            previous_physical = line
            continue
        joined.append(line)
        previous_physical = line
    return "\n".join(joined)


def normalize_capture_text(text: str) -> str:
    """Strip terminal control bytes before classifying captured pane text."""
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    normalized = _ANSI_ESCAPE_RE.sub("", normalized)
    normalized = _CONTROL_CHAR_RE.sub("", normalized)
    return _join_visual_wraps(normalized)


def is_dangerous(cmd_line: str) -> bool:
    """Return True when the shared YOLO rule engine would not auto-approve this command.

    evaluate the USER's active ruleset (the same one the worker acts on), not the built-in
    default, so the UI danger badge matches the real auto-approve decision. Any non-approve outcome
    (block / decline / ask / unknown) is "dangerous" for badge purposes — only a clean approve is safe.
    The catastrophic hard floor still flags regardless of the ruleset.
    """
    cmd_line = cmd_line.strip()
    if not cmd_line:
        return False

    if yolo_rules.hard_floor_decision(cmd_line):
        return True

    ruleset, error = yolo_rules.cached_rules()
    if error or ruleset is None:
        # Fall back to the built-in default ruleset when the user's file is missing/broken.
        ruleset = yolo_rules.validate_rules(yolo_rules.default_rule_data("approve"), source="built-in")
    return yolo_rules.evaluate_ruleset(cmd_line, ruleset)["action"] != "approve"


# ---------------------------------------------------------------------------
# Command extraction from pane text
# ---------------------------------------------------------------------------

# Lines to skip when collecting command text.
_SKIP_LINE = re.compile(
    r"^("
    r"─+$"
    r"|Bash command\b"
    r"|Permission rule\b"
    r"|Do you want"
    r"|Running"
    r"|Esc to cancel"
    r")",
    re.IGNORECASE,
)

# Lines that look like commands (contain special shell chars, flags, or are long).
_CMD_CHARS = re.compile(r"[/|&;$=(>`~]|--|\s-[a-zA-Z]")
# Claude's per-step progress/description marker `[i/N] <description>`, e.g.
# `[1/10] Create the build output directory`. This is DESCRIPTION prose, not the command — but the
# `/` in `[1/10]` matches _CMD_CHARS, so without this guard the whole line folds into the danger
# string ("mkdir -p build/output [1/10] Create the build output directory") and classification skews.
# Treat the step marker (and the description it leads) as chrome.
_STEP_MARKER_RE = re.compile(r"^\[\d+\s*/\s*\d+\]")
# the canonical Claude bullet `● Bash(<cmd>)` / `• Bash(<cmd>)` — the parenthesized arg is
# the exact command, so anchoring to it avoids folding the adjacent description prose.
_BASH_CALL_RE = re.compile(r"[●•]\s*Bash\((.+)\)\s*$")
_CODEX_COMMAND_PROMPT_PREFIX = "would you like to run the following comm"


def _is_codex_command_prompt_line(line: str) -> bool:
    # Real Codex captures can crop the final "d?" at narrow widths, leaving
    # "Would you like to run the following comman"; match the stable prefix.
    return _CODEX_COMMAND_PROMPT_PREFIX in str(line or "").lower()


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
    pane_text = normalize_capture_text(pane_text)
    lines = pane_text.splitlines()

    # Codex: command is on a "$ ..." line below the question.
    for i, line in enumerate(lines):
        if _is_codex_command_prompt_line(line):
            for j in range(i + 1, min(i + 12, len(lines))):
                stripped = lines[j].lstrip()
                # Codex prefixes the command with "$ " after leading box whitespace.
                if stripped.startswith("$ "):
                    cmd = stripped[2:].strip()
                    # Heredocs span their own delimited body; keep the existing single-line handling.
                    if "<<" in cmd:
                        return cmd or None
                    # do NOT early-return on the first shlex-complete prefix — a wrapped
                    # command (e.g. `$ git push` continuing with `--force-with-lease`) would classify
                    # on the safe-looking prefix and auto-approve the dangerous tail. ALWAYS gather every
                    # continuation line up to the selector/stop boundary and classify the FULL command.
                    parts = [cmd] if cmd else []
                    saw_boundary = False
                    for k in range(j + 1, min(j + 20, len(lines))):
                        if _codex_command_stop_line(lines[k]):
                            saw_boundary = True
                            break
                        continuation = lines[k].strip()
                        if continuation:
                            parts.append(continuation)
                    # A capture that ends WITHOUT the selector may be truncated mid-command — treat it as
                    # incomplete and return None so the caller falls to `ask` instead of trusting a prefix.
                    if not saw_boundary:
                        return None
                    joined = " ".join(parts).strip()
                    return joined or None
                # Stop searching once we hit the selector; the command should
                # have appeared by then.
                if _YES_SELECTOR_RE.search(lines[j]):
                    break
            # No "$ " line found; fall through to the Claude path in case
            # this was a stale Codex header above a Claude prompt below.

    # Claude: walk backward from the trigger line to a separator/bullet.
    trigger_idx = None
    for i, line in enumerate(lines):
        if "Permission rule" in line or _CLAUDE_PROCEED_PROMPT_RE.search(line) or "Do you want to make this edit" in line:
            trigger_idx = i
            break

    if trigger_idx is None:
        return None

    # find the top of the CURRENT prompt block FIRST, so neither the `● Bash(<cmd>)` anchor
    # search nor the command gather can cross a `─────` separator (or a prior `●` transcript bullet) into
    # the PREVIOUS step and return its (stale, often safe-looking) command. Before this fix the unbounded
    # backward search grabbed the prior `● Bash(chmod …)` when the live prompt — correctly matching real
    # Claude — shows the command in the box with no `● Bash()` until after approval.
    top_idx = 0
    found_content = False
    for i in range(trigger_idx - 1, -1, -1):
        stripped = lines[i].strip()

        if re.match(r"^─+$", stripped):
            top_idx = i + 1
            break

        if stripped.startswith("●"):
            # a `● Bash(<cmd>)` bullet at the block top IS the command (the post-approval
            # transcript form); otherwise the bullet just bounds the block (prior step's output).
            bash_call = _BASH_CALL_RE.search(lines[i])
            if bash_call:
                return bash_call.group(1).strip() or None
            top_idx = i + 1
            break

        if not stripped and found_content:
            top_idx = i + 1
            break

        if stripped:
            found_content = True

    # anchor to the canonical `● Bash(<cmd>)` arg WITHIN the block (bounded by top_idx — never
    # crosses into a prior step) so we never fold the adjacent description prose into the danger string.
    for i in range(top_idx, trigger_idx):
        bash_call = _BASH_CALL_RE.search(lines[i])
        if bash_call:
            return bash_call.group(1).strip() or None

    cmd_parts: list[str] = []
    for i in range(top_idx, trigger_idx):
        stripped = lines[i].strip()
        if not stripped:
            continue
        if _SKIP_LINE.match(stripped):
            continue
        # The `[i/N] <description>` progress marker is a DESCRIPTION line, never the command. Drop it
        # before the _CMD_CHARS filter (whose `/` clause would otherwise fold "[1/10] ..." into the cmd).
        if _STEP_MARKER_RE.match(stripped):
            continue
        # #79: only fold genuinely command-ish lines. Dropped the `len > 60` fallback — it pulled long
        # DESCRIPTION prose into the "command" and skewed the danger verdict; a long command almost
        # always carries a shell metacharacter (caught by _CMD_CHARS), and the ● Bash(...) anchor above
        # already handles the canonical form.
        if _CMD_CHARS.search(stripped):
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
_CLAUDE_PROCEED_PROMPT_RE = re.compile(r"^\s*(?:[●•]\s*)?Do you want to proceed\?\s*$", re.IGNORECASE)
_PLAN_PROMPT_RE = re.compile(
    r"(?:"
    r"Do you want to (?:proceed with|approve|accept|use) (?:this )?plan\?"
    r"|Would you like (?:me|Claude) to (?:start|proceed|make changes)(?: with this plan)?\?"
    r"|Claude has written up a plan\b.*Would you like to proceed\?"
    r"|Ready to (?:start|make changes)\?"
    r")",
    re.IGNORECASE,
)


def _is_plan_prompt_at(lines: list[str], index: int) -> bool:
    return bool(_plan_prompt_text_at(lines, index))


def _plan_prompt_text_at(lines: list[str], index: int) -> str:
    line = lines[index] if 0 <= index < len(lines) else ""
    if _PLAN_PROMPT_RE.search(line):
        return line.strip()
    if "Claude has written up a plan" not in line:
        return ""
    parts: list[str] = []
    for part in lines[index:index + 4]:
        stripped = part.strip()
        if not stripped:
            continue
        parts.append(stripped)
        if "Would you like to proceed?" in stripped:
            break
    window = " ".join(parts)
    return window if "Would you like to proceed?" in window else ""


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
    pane_text = normalize_capture_text(pane_text)
    last_type = None
    codex_body_until = -1
    lines = pane_text.splitlines()
    for i, line in enumerate(lines):
        if _CLAUDE_PROCEED_PROMPT_RE.search(line):
            last_type = "bash"
        elif _is_codex_command_prompt_line(line):
            last_type = "bash"
            codex_body_until = i + 20
        elif _FILE_PROMPT_RE.search(line):
            last_type = "file"
        elif _is_plan_prompt_at(lines, i):
            last_type = "plan"
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
    """Check that the first option is currently highlighted by an ACTUAL selector glyph (❯/›/>).

    do NOT authorize from a positional "option 1 is Yes before No" guess — on a redraw
    frame with nothing highlighted that wrongly reports the first option as selected, which can confirm
    the wrong option. A send requires a visible selector glyph.
    """
    return bool(_YES_SELECTOR_RE.search(normalize_capture_text(pane_text)))


def selected_prompt_option(pane_text: str) -> int:
    # #67: only a visible selector glyph counts as a selection — no positional default-to-yes.
    matches = list(_SELECTED_CHOICE_NUMBER_RE.finditer(normalize_capture_text(pane_text)))
    if matches:
        return int(matches[-1].group(1))
    return 0


PROMPT_ACTION = {
    "bash": "option1",
    "file": "option2",
    "plan": "option1",
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
    pane_text = normalize_capture_text(pane_text)
    text = ""
    codex_body_until = -1
    lines = pane_text.splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if _CLAUDE_PROCEED_PROMPT_RE.search(stripped):
            text = "Do you want to proceed?"
        elif _is_codex_command_prompt_line(stripped):
            text = "Would you like to run the following command?"
            codex_body_until = i + 20
        else:
            file_match = _FILE_PROMPT_RE.search(stripped)
            if file_match:
                text = stripped
            else:
                plan_text = _plan_prompt_text_at(lines, i)
                if plan_text:
                    text = plan_text
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
    if prompt_type == "plan":
        return "plan approval prompt is visible"
    return ""


_WORKING_SPINNER_GLYPHS = "✢✣✤✥✦✧✩✱✲✳✴✵✶✷✸✹✺✻✽✾✿*+·•◦∙⋅"
_WORKING_LEADING_SYMBOL_RE = rf"(?:[{re.escape(_WORKING_SPINNER_GLYPHS)}]|[^\w\s])"
_WORKING_LINE_RE = re.compile(
    r"(?:"
    rf"^\s*{_WORKING_LEADING_SYMBOL_RE}\s+\S.*(?:…|\.{{3}}).*"
    r"(?:\([^)]*(?:thinking|tokens|effort|esc\s+to\s+interrupt|[↑↓]|\b\d+(?:\.\d+)?\s*[smh]\b)[^)]*\)|\b\d+(?:\.\d+)?\s*[smh]\b)"
    r"|^[^\n]*\([^)]*\besc\s+to\s+interrupt\b[^)]*\)"
    # Claude Code auto-compact progress line: "Compacting conversation... (Nm Ns → Nk tokens) NN%"
    # Starts with a capital letter (no leading symbol), so the symbol-gated branch above misses it.
    r"|^\s*Compacting\s+conversation\.{3}.*\([^)]*(?:tokens|→)[^)]*\)"
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
_STATUS_COUNTER_STALE_SECONDS = 75.0
_STATUS_COUNTER_LEADING_MARKERS = "✢✣✤✥✦✧✩✱✲✳✴✵✶✷✸✹✺✻✽✾✿*+·•◦∙⋅.☉○◯●"
_STATUS_COUNTER_MARKER_RE = re.compile(rf"^\s*(?P<marker>[{re.escape(_STATUS_COUNTER_LEADING_MARKERS)}])\s*(?P<body>\S.*)$")
_STATUS_COUNTER_ELAPSED_RE = re.compile(r"\b(?:\d+(?:\.\d+)?[hms]\s*)+\b", re.IGNORECASE)
_STATUS_COUNTER_DURATION_TOKEN_RE = re.compile(r"(?P<value>\d+(?:\.\d+)?)(?P<unit>[hms])\b", re.IGNORECASE)
_STATUS_COUNTER_TOKEN_RE = re.compile(r"(?:[↑↓]\s*)?(?P<count>\d+(?:\.\d+)?)\s*(?P<suffix>[kKmM])?\s+tokens?\b", re.IGNORECASE)
_STATUS_COUNTER_TOOL_USE_RE = re.compile(r"\b(?P<count>\d+)\s+tool uses?\b", re.IGNORECASE)
_STATUS_COUNTER_PAYLOAD_RE = re.compile(r"\b(?:thinking|effort|tokens?|tool uses?|esc\s+to\s+interrupt)\b|[↑↓]", re.IGNORECASE)
_STATUS_COUNTER_BACKGROUND_RE = re.compile(r"^\s*[○◯]\s*\S.*\b\d+(?:\.\d+)?s\s*·\s*(?:[↑↓]\s*)?\d", re.IGNORECASE)
_VISIBLE_STATUS_COUNTER_CACHE: dict[str, dict[str, object]] = {}
_BOX_DRAWING_ONLY_LINE_RE = re.compile(r"^[\s│┃╭╮╰╯┌┐└┘├┤┬┴┼─━═╔╗╚╝╠╣╦╩╬]+$")
_BOXED_EMPTY_INPUT_LINE_RE = re.compile(r"^\s*[│┃]\s*[❯›>]?\s*[█▉▊▋▌▍▎▏_ ]*[│┃]\s*$")
_EFFORT_STATUS_LINE_RE = re.compile(r"^\s*(?:[^\w\s]\s*)?\S+\s+/effort\b", re.IGNORECASE)
_WORK_QUEUE_HINT_RE = re.compile(r"(?:↑/↓\s+to\s+select|enter\s+to\s+view|↑\s+to\s+manage)", re.IGNORECASE)
_CODEX_INPUT_HINT_RE = re.compile(
    r"^\s*(?:[│┃]\s*)?(?:Use\s+/\S+\s+to\s+.+|(?:type|ask)\s+.+)(?:\s*[│┃])?\s*$",
    re.IGNORECASE,
)
_CODEX_MODEL_STATUS_LINE_RE = re.compile(
    r"^\s*(?:gpt|o\d|codex)[A-Za-z0-9_.-]*\s+\S+(?:\s+\S+)?\s+(?:~|/)[^\s]*(?:\s+\d+%\s+context\s+(?:used|left|remaining))?\s*$",
    re.IGNORECASE,
)
_CODEX_PURSUING_GOAL_STATUS_RE = re.compile(r"\bPursuing\s+goal\s*\((?P<duration>[^)]*\d[^)]*)\)", re.IGNORECASE)
_CODEX_GOAL_ACHIEVED_STATUS_RE = re.compile(r"\bGoal\s+achieved\s*\((?P<duration>[^)]*\d[^)]*)\)", re.IGNORECASE)
_CODEX_GOAL_STATUS_RE = re.compile(r"\b(?:Pursuing\s+goal|Goal\s+achieved)\s*\((?P<duration>[^)]*\d[^)]*)\)", re.IGNORECASE)
_CLAUDE_GOAL_ACTIVE_RE = re.compile(
    r"(?:[◉●○◯☉]\s*)?/goal\s+active\s*\((?P<duration>[^)]*\d[^)]*)\)",
    re.IGNORECASE,
)
_COMPLETED_TASKS_RE = re.compile(
    r"\b(?:all\s+\d+\s+tasks?|both\s+goal\s+items)\s+(?:are\s+)?(?:complete|completed|done|finished)\b",
    re.IGNORECASE,
)
_COMPLETED_FOLLOWUP_QUESTION_RE = re.compile(
    r"\b(?P<question>(?:want|would|should|shall|can|could)\s+(?:me|i|we)\b[^?]{0,320}\?)",
    re.IGNORECASE,
)
_SHELL_PROMPT_RE = re.compile(r"[@:][^@\s]+[$#](?:\s+\S.*)?$")
# Header of the Ctrl-T todo overlay, e.g. "11 tasks (0 done, 1 in progress, 10 open)" — also matches
# singular "1 task (...)". The whole overlay is a bounded block: everything below this header is
# persistent chrome rendered under a LIVE prompt, not newer agent output.
_TASK_LIST_HEADER_RE = re.compile(r"^\d+\s+tasks?\s+\(")
_WORK_QUEUE_ROW_RE = re.compile(
    r"^\s*[○●◦]\s+\S.*(?:\b\d+(?:\.\d+)?\s*s\b|↑/↓\s+to\s+select|enter\s+to\s+view|↑\s+to\s+manage)",
    re.IGNORECASE,
)
_CODEX_QUEUED_FOLLOWUP_HEADER_RE = re.compile(r"^[•◦○]\s+Queued\s+follow-up\s+inputs\b", re.IGNORECASE)
_CODEX_QUEUED_FOLLOWUP_EDIT_HINT_RE = re.compile(r"^shift\s+\+\s+←\s+edit\s+last\s+queued\s+message$", re.IGNORECASE)
_CHOICE_LINE_RE = re.compile(r"^\s*(?:menu:\s*)?(?:[❯›>]\s*)?\d+[.:]\s+\S", re.IGNORECASE)
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
    # also cover Claude's AskUserQuestion footer parts — the arrow cluster "↑/↓ to navigate"
    # (and ←/→), and a bare single-letter key like "n to add notes".
    r"^(?:"
    r"\? for shortcuts"
    # accept ONE-OR-MORE key tokens plus an optional parenthetical before "to", so a
    # footer like "ctrl+b ctrl+b (twice) to run in background" is recognized as a footer hint (not a
    # live command/prompt). Single-key footers like "n to add notes" still match.
    r"|(?:(?:esc|escape|enter|return|tab|shift\+tab|ctrl\+[a-z0-9]|cmd\+[a-z0-9]|alt\+[a-z0-9]|option\+[a-z0-9]|↑/↓|←/→|↑|↓|←|→|[a-z])\s+)+(?:\([^)]*\)\s+)?to\s+.+"
    r")$",
    re.IGNORECASE,
)
_QUESTION_RE = re.compile(r"(?:^|\b)(?:Q\d+\s*/\s*\d+\s*:\s*)?.+\?\s*$")
# Claude Code's AskUserQuestion footer. Its distinctive combo ("Enter to select" plus a
# navigate / add-notes / switch-questions hint) identifies the multi-option ask UI, whose selected
# option is box-highlighted (no ❯ glyph) and which puts a preview / "Notes:" / "Chat about this" block
# between the options and the footer.
_ASK_QUESTION_FOOTER_RE = re.compile(
    r"enter\s+to\s+select", re.IGNORECASE
)
_ASK_QUESTION_HINT_RE = re.compile(
    r"to\s+navigate|add\s+notes|switch\s+questions", re.IGNORECASE
)
_ASK_QUESTION_ACCESSIBLE_FOOTER_RE = re.compile(
    r"^Enter selection\s+\[\d+\s*-\s*\d+\],\s+or\s+Escape\s+to\s+cancel:?\s*$",
    re.IGNORECASE,
)
_INTERRUPTED_QUESTION_RE = re.compile(
    r"(?:^|\b)(?:Interrupted\s*[·:.-]\s*)?(What should\s+(?:Claude|Codex|the agent|[\w.-]+)\s+do\s+instead\?)",
    re.IGNORECASE,
)
_OPTION_LINE_RE = re.compile(r"^\s*(menu:\s*)?([❯›>]?)\s*(\d+)[.:]\s+(.+?)\s*$", re.IGNORECASE)
_INLINE_CONFIRMATION_RE = re.compile(r"\?.*(?:\((?:y/n|yes/no)\)|\[(?:y/N|Y/n|yes/no)\])\s*$", re.IGNORECASE)
_QUERY_URL_OR_PATH_RE = re.compile(r"(?:https?://\S+|(?:^|[\s=])(?:[/~]|\.\.?/)\S+)\?(?:[A-Za-z0-9_.%=&/-]*)?$", re.IGNORECASE)
_QUESTION_WORD_RE = re.compile(r"\b(?:who|what|when|where|why|how|should|would|could|can|is|are|do|does|did|which|will)\b", re.IGNORECASE)


def _is_query_url_or_path_output(line: str) -> bool:
    stripped = _clean_prompt_block_line(line)
    if not stripped or not _QUERY_URL_OR_PATH_RE.search(stripped):
        return False
    return not _QUESTION_WORD_RE.search(stripped)


def _prompt_options(visible_text: str) -> list[dict[str, object]]:
    option_groups: list[list[dict[str, object]]] = []
    current_group: list[dict[str, object]] = []
    lines = normalize_capture_text(visible_text).splitlines()
    ask_question_mode = any(_is_ask_user_question_footer(line) for line in lines)
    for line in lines:
        match = _OPTION_LINE_RE.match(line)
        if not match:
            stripped = line.strip()
            if current_group and stripped and _QUESTION_RE.match(_clean_prompt_block_line(line)):
                option_groups.append(current_group)
                current_group = []
                continue
            if current_group and not stripped:
                continue
            if current_group and stripped and (not _is_separator_or_footer(line) or (ask_question_mode and not _is_footer_hint_line(stripped))):
                # Claude's AskUserQuestion UI can put descriptive sub-lines beneath each numbered
                # choice, and current versions may put a separator before "Chat about this".
                # Keep collecting the current option group until a blank/footer boundary.
                continue
            if current_group:
                option_groups.append(current_group)
                current_group = []
            continue
        inline_matches = list(re.finditer(r"(?:(?<=^)|(?<=\s))([❯›>]?)\s*(\d+)[.:]\s+", line))
        if len(inline_matches) > 1:
            for inline_index, inline_match in enumerate(inline_matches):
                label_start = inline_match.end()
                label_end = inline_matches[inline_index + 1].start() if inline_index + 1 < len(inline_matches) else len(line)
                label = line[label_start:label_end].strip()
                if label:
                    current_group.append({
                        "index": int(inline_match.group(2)),
                        "label": re.sub(r"\s+", " ", label).strip(),
                        "selected": bool(inline_match.group(1)),
                    })
            continue
        menu_prefix, marker, number, label = match.groups()
        if menu_prefix or ask_question_mode:
            label = re.split(r"\s+[—-]\s+", label, maxsplit=1)[0]
        current_group.append({
            "index": int(number),
            "label": re.sub(r"\s+", " ", label).strip(),
            "selected": bool(marker),
        })
    if current_group:
        option_groups.append(current_group)
    return option_groups[-1] if option_groups else []


def _infer_agent(visible_text: str, prompt_type: str | None = None) -> str:
    text = normalize_capture_text(visible_text)
    lowered = text.lower()
    if (
        "codex wants to run a shell command" in lowered
        or "codex wants to use an mcp tool" in lowered
        or _is_codex_command_prompt_line(lowered)
        or re.search(r"^\s*(?:gpt|o\d|codex)[A-Za-z0-9_.-]*\b", text, re.MULTILINE)
    ):
        return "codex"
    if (
        "permission rule bash requires confirmation" in lowered
        or "bash command" in lowered
        or "claude" in lowered
        or "do you want to allow" in lowered
        or prompt_type in {"file", "tool", "plan"}
        or _is_ask_user_question_footer(text)
        or "esc to cancel" in lowered
        or (re.search(r"^\s*●\s+\S", text, re.MULTILINE) and not re.search(r"\(\s*y\s*/\s*n\s*\)|\by/n\b", lowered))
    ):
        return "claude"
    return "unknown"


def _prompt_kind(prompt_type: str | None) -> str:
    if prompt_type == "bash":
        return "shell-command"
    if prompt_type == "file":
        return "file-edit"
    if prompt_type == "tool":
        return "tool-approval"
    if prompt_type == "plan":
        return "plan-approval"
    return ""


def _question_prompt_kind(visible_text: str, question: str) -> str:
    lowered = f"{visible_text}\n{question}".lower()
    if "mcp" in lowered and ("would you like to allow" in lowered or "wants to use an mcp tool" in lowered):
        return "tool-approval"
    return "question"


def _matching_evidence_lines(visible_text: str, prompt_type: str | None, question: str = "") -> list[str]:
    evidence: list[str] = []
    codex_body_until = -1
    lines = normalize_capture_text(visible_text).splitlines()
    for index, line in enumerate(lines):
        stripped = _clean_prompt_block_line(line)
        if not stripped:
            continue
        matched = False
        if prompt_type == "bash" and (
            _CLAUDE_PROCEED_PROMPT_RE.search(stripped)
            or _is_codex_command_prompt_line(stripped)
            or stripped.startswith("$ ")
            or stripped.startswith("Bash command")
            or stripped.startswith("Codex wants to run a shell command")
        ):
            matched = True
            if _is_codex_command_prompt_line(stripped):
                codex_body_until = index + 20
        elif prompt_type == "file" and _FILE_PROMPT_RE.search(stripped):
            matched = True
        elif prompt_type == "plan" and _is_plan_prompt_at(lines, index):
            matched = True
        elif prompt_type == "tool" and "Do you want to allow" in stripped and index > codex_body_until:
            matched = True
        elif question and stripped in question.splitlines():
            matched = True
        elif _OPTION_LINE_RE.match(line):
            matched = True

        if matched:
            evidence.append(stripped)
        if len(evidence) >= 8:
            break
    return evidence


def _negative_reason(visible_text: str) -> str:
    visible_text = normalize_capture_text(visible_text)
    if not visible_text.strip():
        return "empty"
    if stale_approval_behind_working(visible_text) or approval_prompt_has_later_activity(visible_text):
        return "stale prompt text has later activity"
    if visible_agent_working(visible_text):
        return "agent is working"
    if any(re.match(r"^\s*[❯›>]\s*(?:\S.*)?$", line) for line in visible_text.splitlines()[-8:]):
        return "idle composer"
    return "no current agent prompt"


def _hash_prompt_parts(*parts: object) -> str:
    normalized: list[str] = []
    for part in parts:
        if isinstance(part, list):
            normalized.extend(str(item) for item in part)
        elif part not in (None, ""):
            normalized.append(str(part))
    return hashlib.md5("\n".join(normalized).encode()).hexdigest()


def _parse_duration_seconds(text: str) -> float | None:
    total = 0.0
    seen = False
    for match in _STATUS_COUNTER_DURATION_TOKEN_RE.finditer(text):
        seen = True
        value = float(match.group("value"))
        unit = match.group("unit").lower()
        if unit == "h":
            total += value * 3600
        elif unit == "m":
            total += value * 60
        else:
            total += value
    return total if seen else None


def _parse_status_duration_seconds(line: str) -> float | None:
    match = _STATUS_COUNTER_ELAPSED_RE.search(line)
    if not match:
        return None
    return _parse_duration_seconds(match.group(0))


def _parse_codex_pursuing_goal_elapsed_seconds(line: str) -> float | None:
    match = _CODEX_PURSUING_GOAL_STATUS_RE.search(line)
    if not match:
        return None
    return _parse_duration_seconds(match.group("duration"))


def _parse_codex_goal_achieved_elapsed_seconds(line: str) -> float | None:
    match = _CODEX_GOAL_ACHIEVED_STATUS_RE.search(line)
    if not match:
        return None
    return _parse_duration_seconds(match.group("duration"))


def _parse_codex_goal_elapsed_seconds(line: str) -> float | None:
    elapsed = _parse_codex_pursuing_goal_elapsed_seconds(line)
    if elapsed is not None:
        return elapsed
    return _parse_codex_goal_achieved_elapsed_seconds(line)


def _parse_claude_goal_elapsed_seconds(line: str) -> float | None:
    match = _CLAUDE_GOAL_ACTIVE_RE.search(line)
    if not match:
        return None
    return _parse_duration_seconds(match.group("duration"))


def _parse_agent_goal_elapsed_seconds(line: str) -> float | None:
    """Return active-goal elapsed time for Claude `/goal active` or Codex goal status."""
    elapsed = _parse_claude_goal_elapsed_seconds(line)
    if elapsed is not None:
        return elapsed
    return _parse_codex_goal_elapsed_seconds(line)


def _parse_agent_active_goal_elapsed_seconds(line: str) -> float | None:
    elapsed = _parse_claude_goal_elapsed_seconds(line)
    if elapsed is not None:
        return elapsed
    return _parse_codex_pursuing_goal_elapsed_seconds(line)


def _parse_status_token_count(line: str) -> int | None:
    matches = list(_STATUS_COUNTER_TOKEN_RE.finditer(line))
    if not matches:
        return None
    match = matches[-1]
    count = float(match.group("count"))
    suffix = (match.group("suffix") or "").lower()
    if suffix == "k":
        count *= 1000
    elif suffix == "m":
        count *= 1000000
    return int(count)


def _parse_status_tool_uses(line: str) -> int | None:
    matches = list(_STATUS_COUNTER_TOOL_USE_RE.finditer(line))
    if not matches:
        return None
    return int(matches[-1].group("count"))


def _status_counter_identity(line: str) -> str:
    identity = _STATUS_COUNTER_ELAPSED_RE.sub("<elapsed>", line.strip())
    identity = _STATUS_COUNTER_TOKEN_RE.sub("<tokens>", identity)
    identity = _STATUS_COUNTER_TOOL_USE_RE.sub("<tool-uses>", identity)
    return re.sub(r"\s+", " ", identity).lower()


def _status_counter_liveness_snapshot(counter: dict[str, object] | None) -> dict[str, object]:
    if not isinstance(counter, dict):
        return {}
    return {
        "status_identity": counter.get("status_identity"),
        "status_elapsed_seconds": counter.get("status_elapsed_seconds"),
        "status_tokens": counter.get("status_tokens"),
        "status_tool_uses": counter.get("status_tool_uses"),
    }


def parse_agent_status_counter(line: str) -> dict[str, object] | None:
    """Parse a visible Claude/Codex activity counter line when the line shape is current UI chrome."""
    stripped = normalize_capture_text(line).strip()
    if not stripped:
        return None

    elapsed_seconds = _parse_status_duration_seconds(stripped)
    tokens = _parse_status_token_count(stripped)
    tool_uses = _parse_status_tool_uses(stripped)
    if elapsed_seconds is None and tokens is None and tool_uses is None:
        return None

    marker_match = _STATUS_COUNTER_MARKER_RE.match(stripped)
    marker = marker_match.group("marker") if marker_match else ""
    has_marker = marker_match is not None
    has_payload = bool(_STATUS_COUNTER_PAYLOAD_RE.search(stripped))
    has_status_text = "…" in stripped or "..." in stripped or "Working" in stripped or "Reviewing" in stripped
    is_parenthesized_status = has_marker and elapsed_seconds is not None and "(" in stripped and ")" in stripped and (has_payload or has_status_text)
    is_codex_status = bool(elapsed_seconds is not None and _WORKING_LINE_RE.search(stripped))
    is_background_row = bool(elapsed_seconds is not None and tokens is not None and _STATUS_COUNTER_BACKGROUND_RE.search(stripped))
    is_multi_agent_subline = bool((tokens is not None or tool_uses is not None) and _CLAUDE_AGENT_TOKEN_SUBLINE_RE.search(stripped))
    if not (is_parenthesized_status or is_codex_status or is_background_row or is_multi_agent_subline):
        return None

    return {
        "status_line": stripped,
        "status_identity": _status_counter_identity(stripped),
        "status_marker": marker,
        "status_elapsed_seconds": elapsed_seconds,
        "status_tokens": tokens,
        "status_tool_uses": tool_uses,
    }


def _last_status_counter(lines: list[str]) -> tuple[int, dict[str, object] | None]:
    last_index = -1
    last_counter = None
    for index, line in enumerate(lines):
        counter = parse_agent_status_counter(line)
        if counter is not None:
            last_index = index
            last_counter = counter
    return last_index, last_counter


def _last_agent_goal_elapsed_seconds(lines: list[str]) -> float | None:
    for line in reversed(lines):
        elapsed = _parse_agent_goal_elapsed_seconds(normalize_capture_text(line).strip())
        if elapsed is not None:
            return elapsed
    return None


def _status_counter_advanced(previous: dict[str, object] | None, current: dict[str, object]) -> bool:
    if not previous:
        return False
    if previous.get("status_identity") != current.get("status_identity"):
        return False
    for key in ("status_elapsed_seconds", "status_tokens", "status_tool_uses"):
        old = previous.get(key)
        new = current.get(key)
        if isinstance(old, (int, float)) and isinstance(new, (int, float)) and new > old:
            return True
    return False


def _status_counter_screen_state(counter: dict[str, object], pane_target: str | None = None, now: float | None = None) -> dict[str, object]:
    now = time.monotonic() if now is None else now
    advanced = False
    stale = False
    last_counter_seen_at = now
    if pane_target:
        previous = _VISIBLE_STATUS_COUNTER_CACHE.get(pane_target)
        previous_counter = previous.get("counter") if previous else None
        advanced = _status_counter_advanced(previous_counter if isinstance(previous_counter, dict) else None, counter)
        previous_liveness = _status_counter_liveness_snapshot(previous_counter if isinstance(previous_counter, dict) else None)
        current_liveness = _status_counter_liveness_snapshot(counter)
        if previous and previous_liveness == current_liveness and not advanced:
            last_advanced_at = float(previous.get("last_advanced_at") or previous.get("first_seen_at") or now)
            stale = now - last_advanced_at > _STATUS_COUNTER_STALE_SECONDS
            last_counter_seen_at = float(previous.get("last_counter_seen_at") or now)
        if not stale:
            _VISIBLE_STATUS_COUNTER_CACHE[pane_target] = {
                "counter": dict(counter),
                "first_seen_at": previous.get("first_seen_at") if previous else now,
                "last_advanced_at": now if advanced or not previous else previous.get("last_advanced_at", now),
                "last_counter_seen_at": now,
            }

    state = {
        "key": "idle" if stale else "working",
        "text": "" if stale else "agent is working",
        "negative_reason": "stale visible status counter" if stale else "agent is working",
        "activity_source": "visible-counter",
        "status_counter_advanced": advanced,
        "last_counter_seen_at": last_counter_seen_at,
    }
    state.update(counter)
    return state


def _is_working_line(line: str) -> bool:
    return bool(parse_agent_status_counter(line) is not None or _WORKING_LINE_RE.search(line) or _CLAUDE_MULTI_AGENT_HEADER_RE.search(line) or _parse_agent_active_goal_elapsed_seconds(line) is not None)


def visible_agent_working(visible_text: str) -> bool:
    """Return True when the visible pane is showing an active thinking/working row.

    Spec: docs/specs/AGENT_PROMPTS_AND_COMMUNICATION.md#detector-principles
    """
    visible_text = normalize_capture_text(visible_text)
    lines = visible_text.splitlines()[-25:]
    working_index = _last_working_index(lines)
    return working_index >= 0 and not _working_line_has_later_prompt(lines, working_index)


def completed_agent_followup_question(visible_text: str) -> str:
    """Return a current agent follow-up after an explicit task-completion summary, if present."""
    lines = normalize_capture_text(visible_text).splitlines()[-80:]
    completion_index = max(
        (index for index, line in enumerate(lines) if _COMPLETED_TASKS_RE.search(line)),
        default=-1,
    )
    if completion_index < 0:
        return ""
    for line in lines[completion_index + 1:]:
        if _is_separator_or_footer(line) or _is_prompt_trailing_ui_line(line):
            continue
        match = _COMPLETED_FOLLOWUP_QUESTION_RE.search(_clean_prompt_block_line(line))
        if match:
            return match.group("question").strip()
    return ""


def visible_agent_status_counter(visible_text: str) -> dict[str, object] | None:
    """Return the newest current visible status counter, ignoring stale rows above real output."""
    visible_text = normalize_capture_text(visible_text)
    lines = visible_text.splitlines()[-25:]
    counter_index, counter = _last_status_counter(lines)
    if counter_index < 0 or counter is None or _working_line_has_later_prompt(lines, counter_index):
        return None
    goal_elapsed_seconds = _last_agent_goal_elapsed_seconds(lines)
    if goal_elapsed_seconds is not None:
        counter = dict(counter)
        counter["goal_elapsed_seconds"] = goal_elapsed_seconds
        counter["display_elapsed_seconds"] = goal_elapsed_seconds
    else:
        counter = dict(counter)
        counter["display_elapsed_seconds"] = counter.get("status_elapsed_seconds")
    return counter


def _working_line_has_later_prompt(lines: list[str], working_index: int) -> bool:
    in_codex_queued_followup = False
    for line in lines[working_index + 1:]:
        stripped = line.strip()
        # Same bounded-overlay rule as approval_prompt_has_later_activity: a Ctrl-T task list below a
        # working row is chrome, not a later prompt. break so real output above the header still counts.
        if _TASK_LIST_HEADER_RE.match(stripped):
            break
        if _CODEX_QUEUED_FOLLOWUP_HEADER_RE.match(stripped):
            in_codex_queued_followup = True
            continue
        if in_codex_queued_followup:
            if not stripped:
                in_codex_queued_followup = False
                continue
            if stripped.startswith("↳") or _CODEX_QUEUED_FOLLOWUP_EDIT_HINT_RE.match(stripped) or line.startswith(("    ", "  ")):
                continue
            in_codex_queued_followup = False
        if not stripped or _is_separator_or_footer(line) or _is_prompt_trailing_ui_line(line):
            continue
        if re.match(r"^[❯›>]\s*$", stripped):
            continue
        if re.match(r"^[❯›>]\s+\S", stripped):
            continue
        if _SHELL_PROMPT_RE.search(stripped):
            return True
        return True
    return False


def _last_approval_prompt_index(lines: list[str]) -> int:
    last_index = -1
    codex_body_until = -1
    for index, line in enumerate(lines):
        if _CLAUDE_PROCEED_PROMPT_RE.search(line) or _is_codex_command_prompt_line(line):
            last_index = index
            if _is_codex_command_prompt_line(line):
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
    visible_text = normalize_capture_text(visible_text)
    lines = visible_text.splitlines()
    prompt_index = _last_approval_prompt_index(lines)
    return (
        prompt_index >= 0
        and _last_working_index(lines) > prompt_index
        and approval_prompt_has_later_activity(visible_text)
    )


def approval_prompt_has_later_activity(visible_text: str) -> bool:
    """Return True when a dismissed prompt remains visible above newer output."""
    visible_text = normalize_capture_text(visible_text)
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

    composer_index = _current_input_prompt_index(lines)
    start = footer_index + 1 if footer_index >= 0 else selected_index + 1
    for index, line in enumerate(lines[start:], start=start):
        stripped = line.strip()
        # The Ctrl-T task overlay is a bounded block under a LIVE prompt: once its header appears,
        # the rest of the pane is overlay chrome, not newer output. break (not return False) so any
        # genuine output ABOVE the header is still evaluated below and still flags a dismissed prompt.
        if _TASK_LIST_HEADER_RE.match(stripped):
            break
        if index == composer_index:
            continue
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
        if _SHELL_PROMPT_RE.search(stripped):
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
    if _ASK_QUESTION_ACCESSIBLE_FOOTER_RE.match(stripped):
        return True
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
    if stripped.startswith("Press enter to confirm") or stripped.startswith("Press y to"):
        return True
    if _EFFORT_STATUS_LINE_RE.match(stripped):
        return True
    if _CODEX_QUEUED_FOLLOWUP_HEADER_RE.match(stripped) or stripped.startswith("↳") or _CODEX_QUEUED_FOLLOWUP_EDIT_HINT_RE.match(stripped):
        return True
    if _WORK_QUEUE_HINT_RE.search(stripped) or _WORK_QUEUE_ROW_RE.match(stripped):
        return True
    if _CODEX_INPUT_HINT_RE.match(stripped) or _CODEX_MODEL_STATUS_LINE_RE.match(stripped) or _CODEX_GOAL_STATUS_RE.search(stripped):
        return True
    if _CLAUDE_AGENT_TOKEN_SUBLINE_RE.search(stripped):
        return True
    if re.match(r"^(?:[⎿└]\s*)?Tip:", stripped, re.IGNORECASE):
        return True
    if re.match(r"^tmux\s+focus-events\s+off\b", stripped, re.IGNORECASE):
        return True
    if re.match(r"^(?:gpt|claude|opus|sonnet)[A-Za-z0-9_.-]*\s+.+\s·\s", stripped, re.IGNORECASE):
        return True
    if _TASK_LIST_HEADER_RE.match(stripped):
        return True
    if re.match(r"^\+\d+\s+(?:pending|more)\b", stripped, re.IGNORECASE):
        return True
    if re.match(r"^\d+%\s+context\s+(?:used|left|remaining)\b", stripped, re.IGNORECASE):
        return True
    # Ctrl-T task/todo list rows shown below an approval prompt (pending ☐, done ☑/☒/◼, active ◐),
    # optionally led by a tree/box connector. Defense-in-depth for a partial overlay (header scrolled
    # off): these are prompt-trailing UI, not new activity. The header break above is the durable fix.
    # include this Claude version's task glyphs — □ (U+25A1) pending, ✓/✔ done, ✗/✘ failed,
    # ◯ (U+25EF) — alongside the U+2610 ballot-box family. Otherwise a working session with a Ctrl-T task
    # list reads as new output (visible_agent_working -> False) and the YO ball stops spinning.
    if re.match(r"^[│├└╰⎿]?\s*[☐☑☒▢▣◻◼◐◓□✓✔✗✘◯]\s+\S", stripped):
        return True
    if stripped.startswith(("◼", "◻", "☑", "☒", "□", "✓", "✔", "✗", "✘", "◯")):
        return True
    if re.fullmatch(r"[❯›>][\s█▉▊▋▌▍▎▏]*", stripped):
        return True
    if re.fullmatch(r'[❯›>]\s+Try\s+(?:"[^"\n]{1,200}"|“[^“”\n]{1,200}”)', stripped):
        return True
    if re.search(r"\b(?:bypass\s+permissions|esc\s+to\s+interrupt|\d+\s+shells?\b)", stripped, re.IGNORECASE):
        return True
    # Claude Code auto-compact countdown footer: "Ns until auto-compact · /model sonnet[Nm"
    if re.search(r"\buntil\s+auto-compact\b", stripped, re.IGNORECASE):
        return True
    return False


def _clip_prompt_lines(lines: list[str], max_lines: int = 12, max_chars: int = 1200) -> str:
    cleaned = [_clean_prompt_block_line(line) for line in lines]
    cleaned = [line for line in cleaned if line and not re.fullmatch(r"got it\.?", line, re.IGNORECASE)]
    text = "\n".join(cleaned[:max_lines])
    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    return text


def _is_ask_user_question_footer(line: str) -> bool:
    return bool(
        (_ASK_QUESTION_FOOTER_RE.search(line) and _ASK_QUESTION_HINT_RE.search(line))
        or _ASK_QUESTION_ACCESSIBLE_FOOTER_RE.match(line.strip())
    )


def ask_user_question_prompt_text(visible_text: str) -> str:
    """Return the question text when the visible pane shows Claude Code's AskUserQuestion UI.

    that UI is NOT a yes/no permission prompt (``detect_prompt`` returns None) and its
    selected option is box-highlighted (no ``❯``), so the generic choice detector misses it. Recognize
    it by its footer ("Enter to select" + a navigate / add-notes / switch-questions hint) below ≥2
    numbered options and a ``?``-question line — even with a preview box / "Notes:" / "Chat about this"
    block sitting between the options and the footer.
    """
    visible_text = normalize_capture_text(visible_text)
    lines = visible_text.splitlines()[-80:]
    footer_indices = [i for i, line in enumerate(lines) if _is_ask_user_question_footer(line)]
    if not footer_indices:
        return ""
    footer_index = footer_indices[-1]
    if _choice_prompt_has_later_activity(lines, footer_index):
        return ""
    head = lines[:footer_index]
    option_indices = [i for i, line in enumerate(head) if _CHOICE_LINE_RE.search(line)]
    if len(option_indices) < 2:
        return ""
    for line in reversed(head[: option_indices[0]]):
        cleaned = _clean_prompt_block_line(line)
        if not cleaned:
            continue
        if _QUESTION_RE.match(cleaned):
            return cleaned
    return ""


def interrupted_user_question_text(visible_text: str) -> str:
    """Return Claude/Codex's current post-interrupt question, even when goal chrome remains visible."""
    visible_text = normalize_capture_text(visible_text)
    lines = visible_text.splitlines()[-80:]
    for index in range(len(lines) - 1, -1, -1):
        cleaned = _clean_prompt_block_line(re.sub(r"^\s*[⎿└]\s*", "", lines[index]))
        match = _INTERRUPTED_QUESTION_RE.search(cleaned)
        if not match:
            continue
        for later in lines[index + 1:]:
            stripped = later.strip()
            if not stripped or _is_separator_or_footer(later) or _is_prompt_trailing_ui_line(later):
                continue
            if re.fullmatch(r"[❯›>][\s█▉▊▋▌▍▎▏]*", stripped):
                continue
            return ""
        prefix = "Interrupted · " if re.search(r"\bInterrupted\b", cleaned, re.IGNORECASE) else ""
        return prefix + match.group(1)
    return ""


def visible_choice_prompt_text(visible_text: str) -> str:
    """Return the current user-question prompt from the visible pane only.

    This intentionally ignores scrollback. If a spinner/working line is visible,
    working wins over older questions still present above the prompt.
    """
    visible_text = normalize_capture_text(visible_text)
    if not visible_text.strip():
        return ""
    interrupted_question = interrupted_user_question_text(visible_text)
    if interrupted_question:
        return interrupted_question
    if detect_prompt(visible_text) is not None or visible_agent_working(visible_text):
        return ""
    if re.search(r"accept edits on\s*\(", visible_text, re.IGNORECASE):
        return ""

    # the box-highlighted AskUserQuestion multi-option UI is a question, not a yes/no prompt.
    ask_question = ask_user_question_prompt_text(visible_text)
    if ask_question:
        return ask_question

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
            if _choice_prompt_has_later_activity(lines, end):
                return ""
            return _clip_prompt_lines(block)

    prompt_index = _current_input_prompt_index(lines)
    if prompt_index < 0:
        return ""
    for line in reversed(lines[:prompt_index]):
        stripped = _clean_prompt_block_line(line)
        if not stripped or _is_footer_hint_line(stripped) or _is_prompt_trailing_ui_line(line) or stripped.startswith(("keivenc@", "$ ")):
            continue
        if line.strip().startswith(("●", "•")) and not (_QUESTION_RE.match(stripped) or _INLINE_CONFIRMATION_RE.search(stripped)):
            break
        if re.match(r"^\s*[❯›>]\s+\S", line):
            break
        if (_QUESTION_RE.match(stripped) or _INLINE_CONFIRMATION_RE.search(stripped)) and not _is_query_url_or_path_output(stripped):
            return stripped
    return ""


def _current_input_prompt_index(lines: list[str]) -> int:
    """Return the bottom composer prompt row when only trailing UI follows it."""
    start = max(0, len(lines) - 12)
    for index in range(len(lines) - 1, start - 1, -1):
        stripped = lines[index].strip()
        if not re.match(r"^[❯›>](?:\s+\S.*)?$", stripped):
            continue
        later_lines = lines[index + 1:]
        if all(not line.strip() or _is_separator_or_footer(line) or _is_prompt_trailing_ui_line(line) for line in later_lines):
            return index
    return -1


def _choice_prompt_has_later_activity(lines: list[str], end_index: int) -> bool:
    """Return True when a choice prompt is followed by newer output in the same capture."""
    composer_index = _current_input_prompt_index(lines)
    for index, line in enumerate(lines[end_index + 1:], start=end_index + 1):
        stripped = line.strip()
        if not stripped or _is_separator_or_footer(line) or _is_prompt_trailing_ui_line(line):
            continue
        if index == composer_index:
            continue
        if re.match(r"^[❯›>][\s█▉▊▋▌▍▎▏]*$", stripped):
            continue
        return True
    return False


def agent_screen_state(visible_text: str, pane_target: str | None = None, now: float | None = None) -> dict[str, object]:
    """Classify the visible terminal screen for YOLOmux UI badges.

    Approval detection and auto-approval use the same visible pane text as this
    UI state, so the browser does not need its own stale scrollback parser.
    Spec: docs/specs/AGENT_PROMPTS_AND_COMMUNICATION.md#state-model
    """
    visible_text = normalize_capture_text(visible_text)
    prompt_state = approval_prompt_state(visible_text)
    prompt_type = prompt_state.get("type") or None
    if prompt_type is not None:
        return {
            "key": "approval",
            "text": str(prompt_state.get("question_text") or prompt_state.get("text") or prompt_text(visible_text, prompt_type)),
            "prompt_type": prompt_type,
            "agent": prompt_state.get("agent") or "unknown",
            "prompt_kind": prompt_state.get("prompt_kind") or "",
            "question_text": prompt_state.get("question_text") or "",
            "command": prompt_state.get("command"),
            "options": prompt_state.get("options") or [],
            "selected_option": prompt_state.get("selected_option") or 0,
            "confidence": prompt_state.get("confidence") or 0.0,
            "evidence_lines": prompt_state.get("evidence_lines") or [],
            "prompt_hash": prompt_state.get("hash") or "",
        }
    interrupted_question = interrupted_user_question_text(visible_text)
    if interrupted_question:
        return {
            "key": "needs-input",
            "text": interrupted_question,
            "agent": _infer_agent(visible_text),
            "prompt_kind": "question",
            "question_text": interrupted_question,
            "command": None,
            "options": [],
            "selected_option": 0,
            "confidence": 0.75,
            "evidence_lines": _matching_evidence_lines(visible_text, None, interrupted_question) or [interrupted_question],
            "prompt_hash": _hash_prompt_parts(interrupted_question),
        }
    completed_followup = completed_agent_followup_question(visible_text)
    if completed_followup:
        return {
            "key": "needs-input",
            "text": completed_followup,
            "agent": _infer_agent(visible_text),
            "prompt_kind": "question",
            "question_text": completed_followup,
            "command": None,
            "options": [],
            "selected_option": 0,
            "confidence": 0.9,
            "evidence_lines": _matching_evidence_lines(visible_text, None, completed_followup) or [completed_followup],
            "prompt_hash": _hash_prompt_parts(completed_followup),
        }
    counter = visible_agent_status_counter(visible_text)
    if counter is not None:
        return _status_counter_screen_state(counter, pane_target=pane_target, now=now)
    if visible_agent_working(visible_text):
        return {"key": "working", "text": "agent is working", "negative_reason": "agent is working"}
    question = visible_choice_prompt_text(visible_text)
    if question:
        options = _prompt_options(visible_text)
        option_labels = [str(option["label"]) for option in options]
        prompt_kind = _question_prompt_kind(visible_text, question)
        return {
            "key": "needs-input",
            "text": question,
            "agent": _infer_agent(visible_text),
            "prompt_kind": prompt_kind,
            "question_text": question,
            "command": None,
            "options": options,
            "selected_option": selected_prompt_option(visible_text),
            "confidence": 0.9 if options else 0.75,
            "evidence_lines": _matching_evidence_lines(visible_text, None, question),
            "prompt_hash": _hash_prompt_parts(question, option_labels),
        }
    return {"key": "idle", "text": "", "negative_reason": _negative_reason(visible_text)}


def prompt_hash(pane_text: str) -> str:
    """Hash the visible approval prompt block to deduplicate repeated polls.

    Claude Yes/No prompts all have the same selector text. Include the command
    block above the selector so two different commands do not look like the
    same already-approved prompt.
    """
    pane_text = normalize_capture_text(pane_text)
    all_lines = pane_text.splitlines()
    selector_re = re.compile(r"[❯›>]\s*\d+\.\s*\S")
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
    Spec: docs/specs/AGENT_PROMPTS_AND_COMMUNICATION.md#claude-approval-patterns
    and docs/specs/AGENT_PROMPTS_AND_COMMUNICATION.md#codex-approval-patterns.
    """
    visible_text = normalize_capture_text(visible_text)
    pane_text = normalize_capture_text(pane_text) if pane_text is not None else None
    prompt_type = detect_prompt(visible_text)
    selected = yes_is_selected(visible_text)
    if prompt_type is not None and (
        stale_approval_behind_working(visible_text) or approval_prompt_has_later_activity(visible_text)
    ):
        prompt_type = None

    question = prompt_text(visible_text, prompt_type) if prompt_type is not None else ""
    options = _prompt_options(visible_text) if prompt_type is not None else []
    state: dict[str, object] = {
        "visible": prompt_type is not None,
        "type": prompt_type or "",
        "text": question,
        "yes_selected": selected if prompt_type is not None else False,
        "selected_option": selected_prompt_option(visible_text) if prompt_type is not None else 0,
        "action": None,
        "command": None,
        "dangerous": False,
        "hash": prompt_hash(visible_text) if prompt_type is not None else "",
        "signature": prompt_hash(visible_text) if prompt_type is not None else "",
        "agent": _infer_agent(visible_text, prompt_type) if prompt_type is not None else "unknown",
        "prompt_kind": _prompt_kind(prompt_type),
        "question_text": question,
        "options": options,
        "confidence": 0.95 if prompt_type is not None and options else 0.0,
        "evidence_lines": _matching_evidence_lines(visible_text, prompt_type, question) if prompt_type is not None else [],
        "negative_reason": "" if prompt_type is not None else _negative_reason(visible_text),
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
    "normalize_capture_text",
    "parse_agent_status_counter",
    "prompt_hash",
    "prompt_text",
    "selected_prompt_option",
    "stale_approval_behind_working",
    "visible_agent_working",
    "visible_agent_status_counter",
    "visible_choice_prompt_text",
    "yes_is_selected",
]
