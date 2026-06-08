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
        "language": "system",
        "reload_on_update": False,
        "reload_on_update_auto": False,
        "startup_tips": True,
    },
    "appearance": {
        "theme": "dark",
        "terminal_theme": "follow-app",
        "date_time_hour_cycle": "24",
        "ui_font_size": 13,
        "terminal_font_size": 13,
        "editor_font_size": 13,
        "preview_font_size": 14,
        "editor_color_scheme": "dark",
        "editor_dark_color_scheme": "dark",
        "editor_light_color_scheme": "yolomux-light",
        "editor_cursor_style": "block",
        "editor_cursor_color": "yellow",
        "file_explorer_font_size": 13,
        "tab_width": 180,
        "pane_spacing": 4,
        "pane_ring_opacity": 75,
        "inactive_pane_opacity": 60,
        "active_color": "green",
        "max_tabs_per_pane": 10,
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
        # DOIT.29: watched PRs poll on their OWN, longer interval so a big watchlist doesn't burn the
        # GitHub rate limit at the (15s) session-metadata cadence.
        "watched_pr_refresh_ms": 60000,
    },
    "notifications": {
        "toast_duration_ms": 10000,
        "notify_transitions": ["needs-input", "needs-approval", "blocked"],
        "throttle_seconds": 60,
    },
    # DOIT.29: PRs to watch independently of any open session's branch. Each entry is "owner/repo#N" or
    # a full https://github.com/owner/repo/pull/N URL (normalized when polled).
    "github": {
        "watched_prs": [],
    },
    "terminal_editor": {
        "scrollback": 5000,
        "word_wrap": False,
        "line_numbers": False,
    },
    "editor": {
        "autosave": True,
        "autosave_delay_seconds": 2.5,
        "blame_all_lines": False,
    },
    "file_explorer": {
        "root_mode": "sync",
        "image_open_mode": "same-tab",
        "image_preview_max_px": 320,
        "quick_access_paths": ["~", "/", "/tmp"],
        "indexed_dirs": [],
        "index_refresh_seconds": 120,
        "companion_dirs": [],
        "refresh_seconds": 15,
        "new_entry_highlight_ms": 60000,
    },
    "uploads": {
        "filename_template": DEFAULT_UPLOAD_FILENAME_TEMPLATE,
        "max_bytes": UPLOAD_MAX_BYTES,
    },
    "yoagent": {
        "backend": "auto",
        "invocation": "cli",
        "auto_refresh": False,
        "refresh_interval_seconds": 120,
        "system_prompt": "You are YO!agent, a concise assistant for YOLOmux. Use the supplied YOLOmux concepts, activity context, and capability facts as the starting point. Answer the user's question directly in a normal status-update style. Prioritize fresh work, blockers, PR/CI state, dirty repos, changed files, and likely next actions. YOLOmux can read tmux panes, poll sessions, monitor prompts/PRs/files, notify on configured transitions, and send tmux input through explicit admin UI paths. YO!agent chat itself does not currently have autonomous command-sending tools, so do not claim you can directly run commands in another tmux pane. Whenever you discuss session-specific work, refer to it as tmux session `<session-name>` and pair that name with its full directory or repo path enclosed in backticks. If the agent/model matters, say tmux session `<session-name>` with <agent/model> about ... . Avoid session inventories unless the user asks about a session, asks for a summary, asks to list/enumerate sessions, or asks for all sessions. Do not invent missing facts.",
        "intro": "Use the live AI agent activity only as much as the user asked for. If needed facts are missing, say what the user can inspect in YOLOmux instead of inventing details. If the user is unsure what to do, recommend what to work on next based on freshness, importance, blockers, PR/CI state, dirty repos, changed files, and stale work.",
        "format": "Reply in Markdown. Default shape: a short direct answer, then optional bullets for the top relevant topics or next actions. Include repo/directory and important files when they matter. Include session names only when the user asks about a specific session, asks for a summary, or asks to list/enumerate/show all sessions. For summary/list answers, use one Markdown table with columns: tmux session, full path, last worked, details. In the tmux session column, show only the session name as a Markdown link with code-formatted text, like [`2`](?yoagent-session=2). Do not repeat the words tmux session inside table cells. In the full path column, use absolute full directory/repo paths enclosed in backticks, e.g. `/home/<user>/repo`. In the last worked column, use compact recency such as `9 hrs ago` or `5 min ago`. In the details column, write 1-2 factual sentences about what that session is doing. If there are 6 sessions, emit 6 table rows. End with `**Open / pending:**` only for concrete next actions or blockers.",
    },
    "yolo": {
        "rule_file_path": "~/.config/yolomux/yolo-rules.yaml",
        "dry_run": False,
        "prompt_source": "hybrid",
    },
}

LEGACY_YOAGENT_DEFAULTS = {
    "system_prompt": "You are YO!agent, a concise assistant for YOLOmux. Use the supplied YOLOmux concepts and activity context as the starting point. Answer the user's question directly in a normal status-update style. Prioritize fresh work, blockers, PR/CI state, dirty repos, changed files, and likely next actions. You may run tools such as ls, git status, or transcript inspection when that helps answer the user's question. Keep tool use scoped to the relevant tmux session, repo, and transcript paths. Whenever you discuss session-specific work, refer to it as tmux session `<session-name>` and pair that name with its full directory or repo path enclosed in backticks. If the agent/model matters, say tmux session `<session-name>` with <agent/model> about ... . Avoid session inventories unless the user asks about a session, asks for a summary, asks to list/enumerate sessions, or asks for all sessions. Do not invent missing facts.",
    "intro": "Summarize the live AI agent activity only as much as the user asked for. Lead with the freshest or most important task, especially active sessions, recent edits, blocked work, PRs, CI, or dirty repos. Stale or old sessions should be mentioned only in a short follow-up paragraph unless the user explicitly asks for a full list.",
    "format": "Reply in Markdown. Default shape: a short direct answer, then optional bullets only for the top relevant <topic> items. Do not create one item per session unless the user asks to list, enumerate, or show all sessions. When the user does ask for a list, use one numbered item per session/topic, with a bold title and one or two short factual sub-bullets. End with `**Open / pending:**` only when there are concrete next actions, blockers, or stale sessions worth calling out. Name repos, tickets, PRs, and session ids when they matter; omit unsupported details.",
}
LEGACY_YOAGENT_PROMPT_MARKERS = {
    "system_prompt": [
        "Do not mention session ids, per-session details",
        "report only from the supplied agent activity context",
        "Do not run tools or inspect ~/.claude",
        "transcript directories, or any filesystem path",
        "You may run tools such as ls",
    ],
    "intro": [
        "Keep stale or old work out of the default answer",
        "Stale or old sessions should be mentioned only",
    ],
    "format": [
        "Do not include session ids, per-session headings",
        "Do not create one item per session unless",
        "When the user explicitly asks for sessions",
    ],
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

# DOIT.29: notifiable watched-PR transitions, opt-in alongside the session-state keys above.
PR_TRANSITION_KEYS = {
    "pr-merged",
    "pr-ci-failing",
    "pr-review",
}

# The full allowlist accepted in notifications.notify_transitions (session states + PR transitions).
NOTIFY_TRANSITION_KEYS = SESSION_STATE_KEYS | PR_TRANSITION_KEYS

SETTING_LIMITS: dict[tuple[str, str], tuple[float, float]] = {
    ("appearance", "ui_font_size"): (6, 20),
    ("appearance", "terminal_font_size"): (6, 28),
    ("appearance", "editor_font_size"): (6, 28),
    ("appearance", "preview_font_size"): (6, 32),
    ("appearance", "file_explorer_font_size"): (6, 24),
    ("appearance", "tab_width"): (120, 420),
    ("appearance", "pane_spacing"): (0, 20),
    ("appearance", "pane_ring_opacity"): (5, 100),
    ("appearance", "inactive_pane_opacity"): (0, 100),
    ("appearance", "max_tabs_per_pane"): (2, 30),
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
    ("performance", "watched_pr_refresh_ms"): (15000, 600000),
    ("yoagent", "refresh_interval_seconds"): (30, 3600),
    ("notifications", "toast_duration_ms"): (1000, 60000),
    ("notifications", "throttle_seconds"): (0, 600),
    ("terminal_editor", "scrollback"): (1000, 50000),
    ("editor", "autosave_delay_seconds"): (0.5, 60),
    ("file_explorer", "image_preview_max_px"): (120, 1200),
    ("file_explorer", "index_refresh_seconds"): (0, 3600),
    ("file_explorer", "refresh_seconds"): (1, 60),
    ("file_explorer", "new_entry_highlight_ms"): (0, 600000),
    ("uploads", "max_bytes"): (1 * 1024 * 1024, 512 * 1024 * 1024),
}

SETTING_CHOICES: dict[tuple[str, str], set[str]] = {
    ("general", "default_layout"): {"single", "grid", "wall"},
    # i18n (DOIT.8 Phase 0): only locales that ship a catalog are accepted; "system" matches the
    # browser/OS. Phase 1 will widen this as real locale catalogs are added.
    ("general", "language"): {"system", "en", "zh-Hant", "zh-Hans", "es", "ja", "de", "fr", "pt-BR", "ru", "ko", "hi", "ar", "he", "en-XA"},
    ("appearance", "theme"): {"system", "dark", "light"},
    ("appearance", "active_color"): {"green", "blue", "orange", "yellow", "purple", "white"},
    ("appearance", "terminal_theme"): {"dark", "light", "follow-app"},
    ("appearance", "date_time_hour_cycle"): {"24", "12"},
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
    ("appearance", "editor_cursor_color"): {"yellow", "theme"},
    ("file_explorer", "root_mode"): {"fixed", "sync"},
    ("file_explorer", "image_open_mode"): {"same-tab", "new-tab"},
    ("yoagent", "backend"): {"auto", "deterministic", "claude", "codex"},
    ("yoagent", "invocation"): {"cli", "api-key"},
    ("yolo", "prompt_source"): {"pane", "hybrid"},
}

SETTING_COMMENTS: dict[tuple[str, str], str] = {
    ("general", "auto_focus"): "true/false. Default false. When false, layout switches and hover gestures do not move focus or auto-open menus, panes, terminals, editors, Finder/File Explorer, Preferences, or other views.",
    ("general", "default_layout"): "single | grid | wall. Reserved default for new visits.",
    ("general", "language"): "UI language. system matches the browser/OS; otherwise a locale code with a shipped catalog (en, zh-Hant, zh-Hans, es, ja, de, fr, pt-BR, ru, ko, hi, ar, he, en-XA pseudo).",
    ("general", "default_sessions"): "List of tmux sessions to prefer on load. Empty means discovered sessions.",
    ("general", "reload_on_update"): "true/false. Default false. When true, an open client shows a 'New version available' banner once the server ships a newer YOLOMUX_VERSION.",
    ("general", "reload_on_update_auto"): "true/false. Default false. When reload_on_update is on, reload immediately instead of showing a banner — but only when it is safe (no unsaved editor changes and not mid-typing).",
    ("general", "startup_tips"): "true/false. Default true. When true, a small startup Tip teaches one YOLOmux feature after the app loads; users can dismiss it or turn Tips off forever.",
    ("file_explorer", "indexed_dirs"): "Directories with a pre-built quick-open index, one path per line. Adding a path indexes it (also via the Finder right-click); removing a line un-indexes it.",
    ("file_explorer", "index_refresh_seconds"): "Seconds, 0-3600. How often the quick-open index is proactively refreshed in the background. 0 = only rebuild when you search.",
    ("file_explorer", "companion_dirs"): "Extra directories always included when computing per-session repo status (branch, dirty count, ahead/behind), one path per line. Useful for companion repos that sit alongside your session workdirs but are rarely the active pane cwd — e.g. ~/dynamo/frontend-crates.",
    ("github", "watched_prs"): "Pull requests to watch independently of any open session, one per line. Each is 'owner/repo#N' or a full https://github.com/owner/repo/pull/N URL. They show in YO!info and can notify on merge / CI / review changes (see notifications.notify_transitions).",
    ("appearance", "theme"): "system | dark | light. Global UI theme for menus, panes, Finder/File Explorer, Preferences, Differ, and editor defaults.",
    ("appearance", "active_color"): "green | blue | orange | yellow | purple | white. Accent color for ACTIVE/FOCUSED UI (active tab, focused-pane ring/glow, chrome strip, file selection, Markdown headings, and YO markers). Green is the default.",
    ("appearance", "terminal_theme"): "dark | light | follow-app. Terminal color theme. Defaults to follow-app (matches the global color theme); a light terminal raises xterm minimumContrastRatio so dark-tuned agent output stays legible.",
    ("appearance", "date_time_hour_cycle"): "24 | 12. Controls date/time displays in Finder/File Explorer and Differ. Default 24.",
    ("appearance", "ui_font_size"): "Pixels, 6-20. Drives tab and compact UI text.",
    ("appearance", "terminal_font_size"): "Pixels, 6-28. Applied live to xterm.js terminals.",
    ("appearance", "editor_font_size"): "Pixels, 6-28. Applied live to editor and preview panes.",
    ("appearance", "editor_color_scheme"): "Legacy active editor color scheme. Kept for compatibility; new UI uses separate dark/light scheme defaults.",
    ("appearance", "editor_dark_color_scheme"): "Dark editor scheme used by the editor dark/light toggle.",
    ("appearance", "editor_light_color_scheme"): "Light editor scheme used by the editor dark/light toggle. Default is VS Code Light+.",
    ("appearance", "editor_cursor_style"): "line | block. CodeMirror caret shape.",
    ("appearance", "editor_cursor_color"): "yellow | theme. yellow matches the active terminal cursor (#ffd000) for a consistent caret across terminal + editor; theme uses the editor scheme's caret color. Applies to both the line and block cursor.",
    ("appearance", "file_explorer_font_size"): "Pixels, 6-24. Applied live to File Explorer/Finder.",
    ("appearance", "tab_width"): "Pixels, 120-420. Drives the pane tab width CSS variable.",
    ("appearance", "pane_spacing"): "Pixels, 0-20. Gap between panes; at 0 they touch and the green active outline covers the divider. The active outline thickens as spacing grows.",
    ("appearance", "pane_ring_opacity"): "Percent, 5-100. Opacity of the green/red pane ring drawn over the content edge.",
    ("appearance", "inactive_pane_opacity"): "Percent, 0-100. Strength of inactive pane gray-out.",
    ("appearance", "max_tabs_per_pane"): "Caps open tabs per pane (2-30); the oldest unused tabs auto-close (LRU) when the limit is exceeded (dirty editors are kept).",
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
    ("performance", "watched_pr_refresh_ms"): "Milliseconds, 15000-600000. How often watched PRs (github.watched_prs) are polled — a longer interval than session metadata so a big watchlist does not exhaust the GitHub rate limit.",
    ("notifications", "toast_duration_ms"): "Milliseconds, 1000-60000. How long in-page notification popups stay visible.",
    ("notifications", "notify_transitions"): "Event keys that may show notifications. Session states (needs-input, needs-approval, blocked, …) plus watched-PR transitions (pr-merged, pr-ci-failing, pr-review). Unknown keys are ignored.",
    ("notifications", "throttle_seconds"): "Seconds, 0-600. Minimum time before repeating a notification signature.",
    ("terminal_editor", "scrollback"): "Lines, 1000-50000. xterm.js scrollback.",
    ("terminal_editor", "word_wrap"): "true/false. Default editor soft-wrap state.",
    ("terminal_editor", "line_numbers"): "true/false. Default editor line-number gutter state.",
    ("editor", "autosave"): "true/false. When true, dirty editor tabs save after the delay when the file has not changed on disk.",
    ("editor", "autosave_delay_seconds"): "Seconds, 0.5-60. Delay before dirty editor tabs auto-save.",
    ("editor", "blame_all_lines"): "true/false. Default false. When inline git blame is on, annotate EVERY line (not just the cursor's current line, the Cursor default).",
    ("file_explorer", "root_mode"): "fixed | sync. Default sync. fixed stays put; sync follows the focused tmux cwd.",
    ("file_explorer", "image_open_mode"): "same-tab | new-tab. same-tab reuses one image viewer while browsing; new-tab keeps one image tab per file.",
    ("file_explorer", "image_preview_max_px"): "Pixels, 120-1200. Maximum width and height for hover image previews.",
    ("file_explorer", "quick_access_paths"): "List of paths for File Explorer shortcuts.",
    ("file_explorer", "refresh_seconds"): "Seconds, 1-60. Refreshes changed File Explorer directories and open files; client-side jitter avoids synchronized polling.",
    ("file_explorer", "new_entry_highlight_ms"): "Milliseconds, 0-600000. How long new File Explorer entries stay highlighted.",
    ("uploads", "filename_template"): "Upload filename template. Supported fields: {date:%Y%m%d}, {seq:03d}, {name}, {ext}. When {name} is empty, a preceding dash is omitted.",
    ("uploads", "max_bytes"): "Bytes, 1048576-536870912. Maximum buffered browser upload size. Prefer rsync for large files.",
    ("yoagent", "backend"): "auto | deterministic | claude | codex. Default auto prefers codex, then claude (whichever is installed AND logged in), else the No agent summary. The deterministic internal value is shown as No agent; explicit Claude/Codex use the selected invocation when available.",
    ("yoagent", "invocation"): "cli | api-key. CLI runs the local agent binary; api-key is reserved and falls back safely today.",
    ("yoagent", "auto_refresh"): "true/false. Default false. When true, YO!agent refreshes per-session transcript summaries in the background after quiet intervals.",
    ("yoagent", "refresh_interval_seconds"): "Seconds, 30-3600. Minimum interval between background transcript-summary updates per tmux session.",
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


def sanitize_settings(raw: Any, coerced: list[str] | None = None) -> dict[str, Any]:
    # DOIT.6 #84: pass a `coerced` list to collect "<section>.<key>" for every PRESENT incoming value
    # that had to be clamped/reverted, so the API can report it instead of silently changing the value.
    defaults = default_settings()
    source = raw if isinstance(raw, dict) else {}
    sanitized = default_settings()
    for section, values in defaults.items():
        incoming = source.get(section, {})
        if not isinstance(incoming, dict):
            incoming = {}
        if section == "file_explorer" and "refresh_seconds" not in incoming and "refresh_ms" in incoming:
            incoming = dict(incoming)
            try:
                legacy_ms = float(incoming["refresh_ms"])
            except (TypeError, ValueError):
                legacy_ms = 3000
            if round(legacy_ms) != 3000:
                incoming["refresh_seconds"] = legacy_ms / 1000
        if section == "general" and "startup_tips" not in incoming and "startup_helpers" in incoming:
            incoming = dict(incoming)
            incoming["startup_tips"] = incoming["startup_helpers"]
        for key, default in values.items():
            present = key in incoming
            value = incoming.get(key, default)
            if section == "yoagent" and key in LEGACY_YOAGENT_DEFAULTS and value == LEGACY_YOAGENT_DEFAULTS[key]:
                value = default
            legacy_markers = LEGACY_YOAGENT_PROMPT_MARKERS.get(key, []) if section == "yoagent" else []
            if isinstance(value, str) and any(marker in value for marker in legacy_markers):
                value = default
            if isinstance(default, bool):
                sanitized[section][key] = coerce_bool(value, default)
            elif isinstance(default, (int, float)) and not isinstance(default, bool):
                lower, upper = SETTING_LIMITS.get((section, key), (-10**9, 10**9))
                number = coerce_number(value, default, lower, upper)
                sanitized[section][key] = number
            elif isinstance(default, list):
                items = coerce_string_list(value, default)
                if (section, key) == ("notifications", "notify_transitions"):
                    items = [item for item in items if item in NOTIFY_TRANSITION_KEYS]
                sanitized[section][key] = items
            elif (section, key) in SETTING_CHOICES:
                sanitized[section][key] = value if isinstance(value, str) and value in SETTING_CHOICES[(section, key)] else default
            elif isinstance(default, str):
                sanitized[section][key] = value if isinstance(value, str) and value.strip() else default
            if coerced is not None and present and sanitized[section][key] != value:
                coerced.append(f"{section}.{key}")
    return sanitized


def merge_settings(base: dict[str, Any], patch: Any, coerced: list[str] | None = None) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    if not isinstance(patch, dict):
        return sanitize_settings(merged, coerced)
    for section, values in patch.items():
        if section not in merged or not isinstance(values, dict):
            continue
        for key, value in values.items():
            if section == "general" and key == "startup_helpers":
                key = "startup_tips"
            if key in merged[section]:
                merged[section][key] = value
    return sanitize_settings(merged, coerced)


def settings_template(settings: dict[str, Any]) -> str:
    def encode_yaml_value(value: Any) -> str:
        # width=4096 keeps long scalars (e.g. the yoagent prompt) on one line so rejoining the
        # dumped lines with a space cannot corrupt folded text; allow_unicode keeps em-dashes literal.
        lines = [line for line in yaml.safe_dump(value, default_flow_style=True, width=4096, allow_unicode=True).splitlines() if line.strip() != "..."]
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
        coerced: list[str] = []
        next_settings = merge_settings(current, patch, coerced)
        _write_settings_file_unlocked(next_settings, path)
        payload = _settings_payload_unlocked(path)
        # DOIT.6 #84: report which patched keys were clamped/reverted so the API/UI can surface it
        # instead of silently changing the value (e.g. ui_font_size:999 -> 20).
        payload["coerced"] = coerced
        return payload
