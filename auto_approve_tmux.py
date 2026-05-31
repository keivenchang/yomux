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
import logging
import os
import re
import signal
import subprocess
import sys
import time

from yolomux_lib.prompt_detector import (
    _find_full_path,
    action_for_bash_prompt,
    action_for_prompt,
    agent_screen_state,
    approval_prompt_has_later_activity,
    approval_prompt_state,
    detect_prompt,
    extract_command,
    is_dangerous,
    prompt_hash,
    prompt_text,
    stale_approval_behind_working,
    visible_agent_working,
    visible_choice_prompt_text,
    yes_is_selected,
)

_DETECTOR_REEXPORTS = (
    action_for_bash_prompt,
    action_for_prompt,
    agent_screen_state,
    approval_prompt_has_later_activity,
    approval_prompt_state,
    detect_prompt,
    extract_command,
    is_dangerous,
    prompt_hash,
    prompt_text,
    stale_approval_behind_working,
    visible_agent_working,
    visible_choice_prompt_text,
    yes_is_selected,
)

log = logging.getLogger("auto_approve")

# If Enter is missed, a current prompt should not be stuck forever behind the
# de-dup hash. Retry only after the exact prompt remains visible briefly.
PROMPT_RETRY_SECONDS = 5.0

# Re-export detector helpers from yolomux_lib.prompt_detector so existing
# callers can keep importing them from this script.

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
    """Run the pytest coverage that replaced the old inline self-test."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    env = os.environ.copy()
    env.setdefault("PYTHONPYCACHEPREFIX", "/tmp/yolomux-pyc")
    env.setdefault("YOLOMUX_CONFIG_DIR", "/tmp/yolomux-test-config")
    env.setdefault("YOLOMUX_STATE_DIR", "/tmp/yolomux-test-state")
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-p",
        "no:cacheprovider",
        os.path.join(script_dir, "tests/test_auto_approve_detector.py"),
        os.path.join(script_dir, "tests/test_yolo_rules.py"),
    ]
    return subprocess.run(cmd, env=env, check=False).returncode == 0


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
