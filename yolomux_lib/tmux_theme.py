# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Apply the YOLOmux Active color preference to live tmux chrome."""

from __future__ import annotations

import subprocess
from typing import Any
from typing import Callable

from .settings import UI_COLOR_CHOICES
from .tmux_utils import cmd_error
from .tmux_utils import tmux
from .tmux_utils import tmux_session_target


TmuxRunner = Callable[[list[str], float], subprocess.CompletedProcess[str]]

DEFAULT_TMUX_THEME_COLOR = "green"

TMUX_THEME_STYLES: dict[str, dict[str, str]] = {
    "green": {"bg": "green", "fg": "black", "current": "green", "border": "green"},
    "blue": {"bg": "#2563eb", "fg": "#ffffff", "current": "#3b82f6", "border": "#1d4ed8"},
    "orange": {"bg": "#f97316", "fg": "#1a0c00", "current": "#fb923c", "border": "#b91c1c"},
    "yellow": {"bg": "#d6a400", "fg": "#1a1500", "current": "#eab308", "border": "#9a6700"},
    "purple": {"bg": "#7c3aed", "fg": "#ffffff", "current": "#a855f7", "border": "#6d28d9"},
    "white": {"bg": "#dfe5ec", "fg": "#0b0e14", "current": "#e8edf2", "border": "#9aa5b3"},
}


def normalized_tmux_theme_color(value: Any) -> str:
    color = str(value or "").strip()
    return color if color in UI_COLOR_CHOICES else DEFAULT_TMUX_THEME_COLOR


def tmux_theme_color_from_settings(settings: dict[str, Any] | None) -> str:
    appearance = settings.get("appearance") if isinstance(settings, dict) else {}
    active_color = appearance.get("active_color") if isinstance(appearance, dict) else None
    return normalized_tmux_theme_color(active_color)


def tmux_style(bg: str, fg: str, extra: str = "") -> str:
    suffix = f",{extra}" if extra else ""
    return f"bg={bg},fg={fg}{suffix}"


def tmux_theme_global_commands(color: str) -> list[list[str]]:
    style = TMUX_THEME_STYLES[normalized_tmux_theme_color(color)]
    status_style = tmux_style(style["bg"], style["fg"])
    current_style = tmux_style(style["current"], style["fg"], "bold")
    return [
        ["set-option", "-g", "status-style", status_style],
        ["set-option", "-g", "status-left-style", status_style],
        ["set-option", "-g", "status-right-style", status_style],
        ["set-window-option", "-g", "window-status-style", status_style],
        ["set-window-option", "-g", "window-status-current-style", current_style],
        ["set-window-option", "-g", "pane-border-style", f"fg={style['border']}"],
        ["set-window-option", "-g", "pane-active-border-style", f"fg={style['bg']}"],
    ]


def tmux_theme_session_commands(color: str, session: str) -> list[list[str]]:
    clean_session = str(session or "").strip()
    if not clean_session:
        return []
    style = TMUX_THEME_STYLES[normalized_tmux_theme_color(color)]
    target = tmux_session_target(clean_session)
    status_style = tmux_style(style["bg"], style["fg"])
    current_style = tmux_style(style["current"], style["fg"], "bold")
    return [
        ["set-option", "-t", target, "status-style", status_style],
        ["set-option", "-t", target, "status-left-style", status_style],
        ["set-option", "-t", target, "status-right-style", status_style],
        ["set-window-option", "-t", target, "window-status-style", status_style],
        ["set-window-option", "-t", target, "window-status-current-style", current_style],
        ["set-window-option", "-t", target, "pane-border-style", f"fg={style['border']}"],
        ["set-window-option", "-t", target, "pane-active-border-style", f"fg={style['bg']}"],
    ]


def tmux_theme_window_commands(color: str, window: str) -> list[list[str]]:
    clean_window = str(window or "").strip()
    if not clean_window:
        return []
    style = TMUX_THEME_STYLES[normalized_tmux_theme_color(color)]
    status_style = tmux_style(style["bg"], style["fg"])
    current_style = tmux_style(style["current"], style["fg"], "bold")
    return [
        ["set-window-option", "-t", clean_window, "window-status-style", status_style],
        ["set-window-option", "-t", clean_window, "window-status-current-style", current_style],
        ["set-window-option", "-t", clean_window, "pane-border-style", f"fg={style['border']}"],
        ["set-window-option", "-t", clean_window, "pane-active-border-style", f"fg={style['bg']}"],
    ]


def tmux_theme_commands_for_existing(color: str, sessions: list[str], windows: list[str]) -> list[list[str]]:
    commands = list(tmux_theme_global_commands(color))
    for session in sessions:
        commands.extend(tmux_theme_session_commands(color, session))
    for window in windows:
        commands.extend(tmux_theme_window_commands(color, window))
    commands.append(["refresh-client", "-S"])
    return commands


def tmux_theme_commands_for_new_session(color: str, session: str) -> list[list[str]]:
    commands = list(tmux_theme_global_commands(color))
    commands.extend(tmux_theme_session_commands(color, session))
    commands.append(["refresh-client", "-S"])
    return commands


def tmux_list_values(args: list[str], runner: TmuxRunner) -> tuple[list[str], list[str]]:
    result = runner(args, timeout=3.0)
    if result.returncode != 0:
        return [], [cmd_error(result, "tmux list failed")]
    return [line.strip() for line in result.stdout.splitlines() if line.strip()], []


def run_tmux_theme_commands(commands: list[list[str]], runner: TmuxRunner) -> list[str]:
    errors: list[str] = []
    for command in commands:
        result = runner(command, timeout=3.0)
        if result.returncode != 0:
            if command == ["refresh-client", "-S"] and "no current client" in f"{result.stderr}\n{result.stdout}".lower():
                continue
            errors.append(cmd_error(result, f"tmux {' '.join(command)} failed"))
    return errors


def apply_tmux_theme_color_to_existing(color: str, runner: TmuxRunner = tmux) -> dict[str, Any]:
    clean_color = normalized_tmux_theme_color(color)
    sessions, session_errors = tmux_list_values(["list-sessions", "-F", "#{session_name}"], runner)
    if session_errors:
        return {"color": clean_color, "applied": False, "commands": 0, "errors": session_errors}
    windows, window_errors = tmux_list_values(["list-windows", "-a", "-F", "#{session_name}:#{window_index}"], runner)
    commands = tmux_theme_commands_for_existing(clean_color, sessions, windows)
    errors = [*window_errors, *run_tmux_theme_commands(commands, runner)]
    return {"color": clean_color, "applied": not errors, "commands": len(commands), "errors": errors}


def apply_tmux_theme_color_to_new_session(session: str, color: str, runner: TmuxRunner = tmux) -> dict[str, Any]:
    clean_color = normalized_tmux_theme_color(color)
    commands = tmux_theme_commands_for_new_session(clean_color, session)
    errors = run_tmux_theme_commands(commands, runner)
    return {"color": clean_color, "applied": not errors, "commands": len(commands), "errors": errors}
