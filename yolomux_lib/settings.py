# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""User preferences for YOLOmux."""

from __future__ import annotations

import copy
import fcntl
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import yaml

from .common import CONFIG_DIR
from .common import DEFAULT_UPLOAD_FILENAME_TEMPLATE
from .common import UPLOAD_MAX_BYTES


SETTINGS_PATH = CONFIG_DIR / "settings.yaml"
SETTINGS_DISPLAY_PATH = "~/.config/yolomux/settings.yaml"
_SETTINGS_LOCK = threading.RLock()

DEFAULT_SETTINGS: dict[str, Any] = {
    "general": {
        "auto_focus": False,
        "default_layout": "single",
        "default_sessions": [],
        "reload_on_update": False,
        "reload_on_update_auto": False,
    },
    "appearance": {
        "theme": "dark",
        "terminal_theme": "dark",
        "ui_font_size": 13,
        "terminal_font_size": 13,
        "editor_font_size": 13,
        "editor_color_scheme": "dark",
        "editor_dark_color_scheme": "dark",
        "editor_light_color_scheme": "vscode-light-plus",
        "editor_cursor_style": "line",
        "file_explorer_font_size": 13,
        "tab_width": 180,
        "red_reminder_ms": 1550,
        "yolo_rotate_ms": 20000,
        "metadata_badge_pulse_seconds": 20,
    },
    "performance": {
        "metadata_refresh_ms": 15000,
        "pane_state_refresh_ms": 1250,
        "latency_refresh_ms": 3000,
        "event_log_refresh_ms": 5000,
        "popover_show_delay_ms": 1000,
        "popover_hide_delay_ms": 300,
        "menu_hover_open_delay_ms": 800,
        "tab_popover_show_delay_ms": 1000,
        "tab_popover_follow_delay_ms": 120,
        "remote_resize_delay_ms": 220,
        "auto_approve_interval_seconds": 0.5,
    },
    "notifications": {
        "toast_duration_ms": 10000,
        "notify_transitions": ["needs-input", "needs-approval", "blocked"],
        "throttle_seconds": 60,
    },
    "terminal_editor": {
        "scrollback": 5000,
        "word_wrap": False,
        "line_numbers": False,
    },
    "editor": {
        "autosave": True,
        "autosave_delay_seconds": 2.5,
    },
    "file_explorer": {
        "root_mode": "fixed",
        "image_open_mode": "same-tab",
        "image_preview_max_px": 320,
        "quick_access_paths": ["~", "/", "/tmp"],
        "indexed_dirs": [],
        "refresh_ms": 3000,
        "new_entry_highlight_ms": 60000,
    },
    "uploads": {
        "filename_template": DEFAULT_UPLOAD_FILENAME_TEMPLATE,
        "max_bytes": UPLOAD_MAX_BYTES,
    },
    "yoagent": {
        "backend": "deterministic",
        "invocation": "cli",
        "system_prompt": "You are YO!agent, a concise assistant for YOLOmux. Help users operate YOLOmux using the supplied concepts and report only from the supplied agent activity context. Write like a normal human status update, not a metadata list. Do not run tools or inspect ~/.claude, ~/.codex, transcript directories, or any filesystem path. Do not say Sup. Do not invent missing facts.",
        "intro": "Start with the user's most recent work, then say what is currently being changed and why. Use per-session last-worked timestamps to call out fresh and stale context. Give a concrete recommendation for what to work on next, plus one short tip for using AI agents more efficiently when useful.",
        "format": "Prefer one short paragraph in this shape: Your most recent work is about <topic>, and you are currently making changes to <files or repos> in order to <goal>. Other work includes <other topics>. Session <id> was last worked <age> ago; session <id> is stale at <age>. You have not touched <area> for quite some time. Recommendation: <next best action>. Efficiency tip: <how to use the AI sessions better>. Omit any clause the context does not support. Put changed-file totals and active/idle agent count after the work summary. Avoid repeating repo, task, or session names.",
    },
    "yolo": {
        "rule_file_path": "~/.config/yolomux/yolo-rules.yaml",
        "dry_run": False,
        "prompt_source": "hybrid",
    },
}

SESSION_STATE_KEYS = {
    "needs-approval",
    "yolo-approval",
    "needs-input",
    "blocked",
    "disconnected",
    "tests-running",
    "ready-review",
    "working",
    "idle",
    "done",
}

SETTING_LIMITS: dict[tuple[str, str], tuple[float, float]] = {
    ("appearance", "ui_font_size"): (8, 20),
    ("appearance", "terminal_font_size"): (8, 28),
    ("appearance", "editor_font_size"): (8, 28),
    ("appearance", "file_explorer_font_size"): (8, 24),
    ("appearance", "tab_width"): (120, 420),
    ("appearance", "red_reminder_ms"): (0, 10000),
    ("appearance", "yolo_rotate_ms"): (0, 60000),
    ("appearance", "metadata_badge_pulse_seconds"): (0, 120),
    ("performance", "metadata_refresh_ms"): (3000, 120000),
    ("performance", "pane_state_refresh_ms"): (500, 30000),
    ("performance", "latency_refresh_ms"): (1000, 30000),
    ("performance", "event_log_refresh_ms"): (1000, 60000),
    ("performance", "popover_show_delay_ms"): (0, 3000),
    ("performance", "popover_hide_delay_ms"): (0, 3000),
    ("performance", "menu_hover_open_delay_ms"): (0, 3000),
    ("performance", "tab_popover_show_delay_ms"): (0, 3000),
    ("performance", "tab_popover_follow_delay_ms"): (0, 1000),
    ("performance", "remote_resize_delay_ms"): (50, 2000),
    ("performance", "auto_approve_interval_seconds"): (0.1, 10),
    ("notifications", "toast_duration_ms"): (1000, 60000),
    ("notifications", "throttle_seconds"): (0, 600),
    ("terminal_editor", "scrollback"): (1000, 50000),
    ("editor", "autosave_delay_seconds"): (0.5, 60),
    ("file_explorer", "image_preview_max_px"): (120, 1200),
    ("file_explorer", "refresh_ms"): (1000, 60000),
    ("file_explorer", "new_entry_highlight_ms"): (0, 600000),
    ("uploads", "max_bytes"): (1 * 1024 * 1024, 512 * 1024 * 1024),
}

SETTING_CHOICES: dict[tuple[str, str], set[str]] = {
    ("general", "default_layout"): {"single", "grid", "wall"},
    ("appearance", "theme"): {"system", "dark", "light"},
    ("appearance", "terminal_theme"): {"dark", "light", "follow-app"},
    ("appearance", "editor_color_scheme"): {
        "dark",
        "one-dark",
        "dracula",
        "monokai",
        "vscode-dark-plus",
        "nord",
        "yolomux-light",
        "github-light",
        "vscode-light-plus",
        "one-light",
        "solarized-light",
    },
    ("appearance", "editor_dark_color_scheme"): {
        "dark",
        "one-dark",
        "dracula",
        "monokai",
        "vscode-dark-plus",
        "nord",
    },
    ("appearance", "editor_light_color_scheme"): {
        "yolomux-light",
        "github-light",
        "vscode-light-plus",
        "one-light",
        "solarized-light",
    },
    ("appearance", "editor_cursor_style"): {"line", "block"},
    ("file_explorer", "root_mode"): {"fixed", "sync"},
    ("file_explorer", "image_open_mode"): {"same-tab", "new-tab"},
    ("yoagent", "backend"): {"deterministic", "claude", "codex"},
    ("yoagent", "invocation"): {"cli", "api-key"},
    ("yolo", "prompt_source"): {"pane", "hybrid"},
}

SETTING_COMMENTS: dict[tuple[str, str], str] = {
    ("general", "auto_focus"): "true/false. Default false. When false, layout switches and hover gestures do not move focus or auto-open menus, panes, terminals, editors, Finder/File Explorer, Preferences, or other views.",
    ("general", "default_layout"): "single | grid | wall. Reserved default for new visits.",
    ("general", "default_sessions"): "List of tmux sessions to prefer on load. Empty means discovered sessions.",
    ("general", "reload_on_update"): "true/false. Default false. When true, an open client shows a 'New version available' banner once the server ships a newer YOLOMUX_VERSION.",
    ("general", "reload_on_update_auto"): "true/false. Default false. When reload_on_update is on, reload immediately instead of showing a banner — but only when it is safe (no unsaved editor changes and not mid-typing).",
    ("file_explorer", "indexed_dirs"): "Directories with a pre-built quick-open index, one path per line. Adding a path indexes it (also via the Finder right-click); removing a line un-indexes it.",
    ("appearance", "theme"): "system | dark | light. Global UI theme for menus, panes, Finder/File Explorer, Preferences, Modified files, and editor defaults.",
    ("appearance", "terminal_theme"): "dark | light | follow-app. Terminal color theme. Default dark because full-screen terminal apps usually assume a dark terminal.",
    ("appearance", "ui_font_size"): "Pixels, 8-20. Drives tab and compact UI text.",
    ("appearance", "terminal_font_size"): "Pixels, 8-28. Applied live to xterm.js terminals.",
    ("appearance", "editor_font_size"): "Pixels, 8-28. Applied live to editor and preview panes.",
    ("appearance", "editor_color_scheme"): "Legacy active editor color scheme. Kept for compatibility; new UI uses separate dark/light scheme defaults.",
    ("appearance", "editor_dark_color_scheme"): "Dark editor scheme used by the editor dark/light toggle.",
    ("appearance", "editor_light_color_scheme"): "Light editor scheme used by the editor dark/light toggle. Default is VS Code Light+.",
    ("appearance", "editor_cursor_style"): "line | block. CodeMirror caret shape; both use the active editor cursor color.",
    ("appearance", "file_explorer_font_size"): "Pixels, 8-24. Applied live to File Explorer/Finder.",
    ("appearance", "tab_width"): "Pixels, 120-420. Drives the pane tab width CSS variable.",
    ("appearance", "red_reminder_ms"): "Milliseconds, 0 disables the attention pulse cycle.",
    ("appearance", "yolo_rotate_ms"): "Milliseconds, 0 disables YO rotation timing.",
    ("appearance", "metadata_badge_pulse_seconds"): "Seconds, 0-120. Duration for PR/branch metadata badge pulses.",
    ("performance", "metadata_refresh_ms"): "Milliseconds, 3000-120000. Refreshes branch, PR, cwd, and process state; client-side jitter avoids synchronized polling.",
    ("performance", "pane_state_refresh_ms"): "Milliseconds, 500-30000. Refreshes YOLO status, prompt state, and tmux session roster; client-side jitter avoids synchronized polling.",
    ("performance", "latency_refresh_ms"): "Milliseconds, 1000-30000. Updates browser-to-server health; client-side jitter avoids synchronized polling.",
    ("performance", "event_log_refresh_ms"): "Milliseconds, 1000-60000. Refreshes open YOLO/event-log panes; client-side jitter avoids synchronized polling.",
    ("performance", "popover_show_delay_ms"): "Milliseconds, 0-3000. Hover delay before regular help and preview popovers open.",
    ("performance", "popover_hide_delay_ms"): "Milliseconds, 0-3000. Delay before popovers close after pointer leaves.",
    ("performance", "menu_hover_open_delay_ms"): "Milliseconds, 0-3000. Hover delay before top menus open.",
    ("performance", "tab_popover_show_delay_ms"): "Milliseconds, 0-3000. First hover delay before a tab details popover opens.",
    ("performance", "tab_popover_follow_delay_ms"): "Milliseconds, 0-1000. Delay when moving between tab details after one is already open.",
    ("performance", "remote_resize_delay_ms"): "Milliseconds, 50-2000. Debounce for tmux remote resize.",
    ("performance", "auto_approve_interval_seconds"): "Seconds, 0.1-10. Poll loop interval for newly enabled YOLO workers.",
    ("notifications", "toast_duration_ms"): "Milliseconds, 1000-60000. How long in-page notification popups stay visible.",
    ("notifications", "notify_transitions"): "State keys that may show notifications. Unknown keys are ignored.",
    ("notifications", "throttle_seconds"): "Seconds, 0-600. Minimum time before repeating a notification signature.",
    ("terminal_editor", "scrollback"): "Lines, 1000-50000. xterm.js scrollback.",
    ("terminal_editor", "word_wrap"): "true/false. Default editor soft-wrap state.",
    ("terminal_editor", "line_numbers"): "true/false. Default editor line-number gutter state.",
    ("editor", "autosave"): "true/false. When true, dirty editor tabs save after the delay when the file has not changed on disk.",
    ("editor", "autosave_delay_seconds"): "Seconds, 0.5-60. Delay before dirty editor tabs auto-save.",
    ("file_explorer", "root_mode"): "fixed | sync. fixed stays put; sync follows the focused tmux cwd.",
    ("file_explorer", "image_open_mode"): "same-tab | new-tab. same-tab reuses one image viewer while browsing; new-tab keeps one image tab per file.",
    ("file_explorer", "image_preview_max_px"): "Pixels, 120-1200. Maximum width and height for hover image previews.",
    ("file_explorer", "quick_access_paths"): "List of paths for File Explorer shortcuts.",
    ("file_explorer", "refresh_ms"): "Milliseconds, 1000-60000. Refreshes changed File Explorer directories and open files; client-side jitter avoids synchronized polling.",
    ("file_explorer", "new_entry_highlight_ms"): "Milliseconds, 0-600000. How long new File Explorer entries stay highlighted.",
    ("uploads", "filename_template"): "Upload filename template. Supported fields: {date:%Y%m%d}, {seq:03d}, {name}, {ext}. When {name} is empty, a preceding dash is omitted.",
    ("uploads", "max_bytes"): "Bytes, 1048576-536870912. Maximum buffered browser upload size. Prefer rsync for large files.",
    ("yoagent", "backend"): "deterministic | claude | codex. The deterministic internal value is shown as No agent; Claude/Codex use the selected invocation when available.",
    ("yoagent", "invocation"): "cli | api-key. CLI runs the local agent binary; api-key is reserved and falls back safely today.",
    ("yoagent", "system_prompt"): "System prompt used when YO!agent calls a model backend.",
    ("yoagent", "intro"): "Instruction prefix added before the activity context.",
    ("yoagent", "format"): "Output-format instruction added before the user's question.",
    ("yolo", "rule_file_path"): "Path to the YOLO rule YAML file. The file's top-level default: value controls fallback behavior.",
    ("yolo", "dry_run"): "true/false. Log rule decisions without acting.",
    ("yolo", "prompt_source"): "pane | hybrid. pane uses visible tmux detection only; hybrid lets recent transcript JSONL rescue prompt type/command only when a selectable prompt is visible.",
}


def default_settings() -> dict[str, Any]:
    return copy.deepcopy(DEFAULT_SETTINGS)


def coerce_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def coerce_number(value: Any, default: int | float, lower: float, upper: float) -> int | float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    clamped = max(lower, min(upper, number))
    if isinstance(default, int):
        return int(round(clamped))
    return round(clamped, 3)


def coerce_string_list(value: Any, default: list[str]) -> list[str]:
    if not isinstance(value, list):
        return list(default)
    result = []
    for item in value:
        if isinstance(item, str) and item.strip():
            result.append(item.strip())
    return result


def sanitize_settings(raw: Any) -> dict[str, Any]:
    defaults = default_settings()
    source = raw if isinstance(raw, dict) else {}
    sanitized = default_settings()
    for section, values in defaults.items():
        incoming = source.get(section, {})
        if not isinstance(incoming, dict):
            incoming = {}
        for key, default in values.items():
            value = incoming.get(key, default)
            if isinstance(default, bool):
                sanitized[section][key] = coerce_bool(value, default)
            elif isinstance(default, (int, float)) and not isinstance(default, bool):
                lower, upper = SETTING_LIMITS.get((section, key), (-10**9, 10**9))
                number = coerce_number(value, default, lower, upper)
                sanitized[section][key] = number
            elif isinstance(default, list):
                items = coerce_string_list(value, default)
                if (section, key) == ("notifications", "notify_transitions"):
                    items = [item for item in items if item in SESSION_STATE_KEYS]
                sanitized[section][key] = items
            elif (section, key) in SETTING_CHOICES:
                sanitized[section][key] = value if isinstance(value, str) and value in SETTING_CHOICES[(section, key)] else default
            elif isinstance(default, str):
                sanitized[section][key] = value if isinstance(value, str) and value.strip() else default
    return sanitized


def merge_settings(base: dict[str, Any], patch: Any) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    if not isinstance(patch, dict):
        return sanitize_settings(merged)
    for section, values in patch.items():
        if section not in merged or not isinstance(values, dict):
            continue
        for key, value in values.items():
            if key in merged[section]:
                merged[section][key] = value
    return sanitize_settings(merged)


def settings_template(settings: dict[str, Any]) -> str:
    def encode_yaml_value(value: Any) -> str:
        lines = [line for line in yaml.safe_dump(value, default_flow_style=True).splitlines() if line.strip() != "..."]
        return " ".join(line.strip() for line in lines).strip() or "''"

    lines = [
        "# YOLOmux user preferences.",
        "# Hand edits are picked up by running servers. UI saves rewrite this file from this template.",
        f"# Path: {SETTINGS_DISPLAY_PATH}",
        "",
    ]
    for section, values in settings.items():
        lines.append(f"{section}:")
        if isinstance(values, dict):
            for key, value in values.items():
                comment = SETTING_COMMENTS.get((section, key))
                if comment:
                    lines.append(f"  # {comment}")
                if isinstance(value, list):
                    if value:
                        lines.append(f"  {key}:")
                        for item in value:
                            lines.append(f"    - {encode_yaml_value(item)}")
                    else:
                        lines.append(f"  {key}: []")
                else:
                    lines.append(f"  {key}: {encode_yaml_value(value)}")
        else:
            lines.append(f"  {encode_yaml_value(values)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


@contextmanager
def locked_settings_file(path: Path = SETTINGS_PATH) -> Any:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    lock_path = path.with_name(f".{path.name}.lock")
    with _SETTINGS_LOCK:
        with lock_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _write_settings_file_unlocked(settings: dict[str, Any], path: Path = SETTINGS_PATH) -> None:
    sanitized = sanitize_settings(settings)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.{time.time_ns()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(settings_template(sanitized))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        path.chmod(0o600)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def write_settings_file(settings: dict[str, Any], path: Path = SETTINGS_PATH) -> None:
    with locked_settings_file(path):
        _write_settings_file_unlocked(settings, path)


def _read_settings_file_unlocked(path: Path = SETTINGS_PATH) -> tuple[dict[str, Any], str]:
    if not path.exists():
        _write_settings_file_unlocked(default_settings(), path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        return default_settings(), str(exc)
    return sanitize_settings(raw), ""


def read_settings_file(path: Path = SETTINGS_PATH) -> tuple[dict[str, Any], str]:
    with locked_settings_file(path):
        return _read_settings_file_unlocked(path)


def _settings_payload_unlocked(path: Path = SETTINGS_PATH) -> dict[str, Any]:
    settings, error = _read_settings_file_unlocked(path)
    stat = path.stat() if path.exists() else None
    return {
        "settings": settings,
        "defaults": default_settings(),
        "path": str(path),
        "display_path": SETTINGS_DISPLAY_PATH,
        "mtime_ns": stat.st_mtime_ns if stat else 0,
        "error": error,
    }


def settings_payload(path: Path = SETTINGS_PATH) -> dict[str, Any]:
    with locked_settings_file(path):
        return _settings_payload_unlocked(path)


def save_settings(patch: Any, path: Path = SETTINGS_PATH) -> dict[str, Any]:
    with locked_settings_file(path):
        current, _ = _read_settings_file_unlocked(path)
        next_settings = merge_settings(current, patch)
        _write_settings_file_unlocked(next_settings, path)
        return _settings_payload_unlocked(path)
