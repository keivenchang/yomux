# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Deterministic YO!agent settings and product-state helpers."""

from __future__ import annotations

import re
import time
from typing import Any
from typing import Callable

from ..metadata import parse_pull_request_ref
from ..settings import NOTIFY_TRANSITION_KEYS
from ..settings import SETTINGS_DISPLAY_PATH
from ..settings import canonical_image_action_order_item
from ..settings import default_settings
from ..settings import settings_catalog
from ..web import server_string
from .transports import BACKEND_REASON_AVAILABLE
from .transports import BACKEND_REASON_MODULE_MISSING
from .transports import BACKEND_REASON_NO_CREDENTIALS
from .transports import BACKEND_REASON_NO_PROVIDER
from .transports import BackendAvailability


# Every visible deterministic YO!agent sentence resolves through this one catalog-backed descriptor owner.


def yoagent_text(locale: str, key: str, **params: Any) -> str:
    replacements = {name: str(value) for name, value in params.items()}
    return server_string(str(locale or "en"), key, **replacements)


def yoagent_user_message_text(locale: str, payload: dict[str, Any], fallback_key: str) -> str:
    descriptor = payload.get("user_message") if isinstance(payload.get("user_message"), dict) else {}
    key = str(descriptor.get("key") or fallback_key).strip()
    params = descriptor.get("params") if isinstance(descriptor.get("params"), dict) else {}
    return yoagent_text(locale, key, **params)



SETTING_NAME_ALIASES: dict[str, tuple[str, ...]] = {
    "appearance.theme": ("theme", "global theme", "color theme", "dark mode", "light mode", "app theme", "background", "background color", "bg"),
    "appearance.active_color": ("active color", "accent color", "focus color", "pane ring color", "ui color", "color"),
    "appearance.separator_color": ("separator color", "split color", "divider color", "drop preview color"),
    "appearance.editor_cursor_color": ("cursor color", "caret color", "editor cursor color", "terminal cursor color"),
    "appearance.editor_cursor_style": ("cursor style", "caret style", "block cursor", "line cursor"),
    "appearance.ui_font_size": ("ui font size", "interface font size", "app font size", "yo!agent font size", "yoagent font size"),
    "appearance.terminal_font_size": ("terminal font size", "tmux font size", "shell font size"),
    "appearance.editor_font_size": ("editor font size", "code font size", "codemirror font size"),
    "appearance.preview_font_size": ("preview font size", "markdown preview font size"),
    "appearance.file_explorer_font_size": ("finder font size", "file explorer font size", "differ font size", "tabber font size"),
    "appearance.tab_width": ("tab width", "tab size", "tab minimum width"),
    "appearance.max_tabs_per_pane": ("max tabs", "maximum tabs", "tabs per pane", "max tabs per pane"),
    "appearance.pane_spacing": ("pane spacing", "split spacing", "pane gap"),
    "appearance.pane_ring_opacity": ("pane ring opacity", "active ring opacity", "focus ring opacity"),
    "appearance.inactive_pane_opacity": ("inactive pane opacity", "inactive dim", "dim inactive panes"),
    "performance.workflow_transition_glow_seconds": ("notification duration", "workflow transition glow", "transition glow", "ball glow", "status ball glow", "green red yellow ball glow", "red yellow green ball glow", "metadata pulse", "badge pulse", "pr badge pulse"),
    "appearance.date_time_hour_cycle": ("hour cycle", "clock format", "12 hour", "24 hour"),
    "appearance.terminal_theme": ("terminal theme", "xterm theme"),
    "appearance.editor_dark_color_scheme": ("dark editor scheme", "dark code theme"),
    "appearance.editor_light_color_scheme": ("light editor scheme", "light code theme"),
    "general.language": ("language", "locale", "ui language"),
    "general.auto_focus": ("auto focus", "hover focus", "focus follows hover"),
    "general.default_layout": ("default layout", "startup layout", "layout mode"),
    "general.reload_on_update": ("reload on update", "update reload banner"),
    "general.reload_on_update_auto": ("auto reload on update", "reload automatically on update"),
    "general.startup_tips": ("startup tips", "tips"),
    "updates.notify_level": ("notify level", "update notify level", "version notify level", "major minor patch"),
    "updates.check_enabled": ("update checker", "check for updates"),
    "updates.check_interval_minutes": ("update check interval", "update interval"),
    "notifications.notify_transitions": ("notify transition", "notify transitions", "notification transition", "notification transitions", "notification events"),
    "notifications.toast_duration_ms": ("toast duration", "notification duration"),
    "notifications.throttle_seconds": ("notification throttle", "notification cooldown"),
    "file_explorer.root_mode": ("finder root mode", "file explorer root mode", "sync root"),
    "file_explorer.image_open_mode": ("image open mode", "image tab mode"),
    "file_explorer.image_preview_max_px": ("image preview size", "image hover preview size"),
    "file_explorer.indexed_dirs": ("indexed dirs", "indexed directories", "quick search directories"),
    "file_explorer.index_refresh_seconds": ("index refresh", "quick search refresh"),
    "file_explorer.companion_dirs": ("companion dirs", "companion repos", "extra repos"),
    "file_explorer.dir_cache_ms": ("directory cache", "finder cache"),
    "file_explorer.new_entry_highlight_ms": ("new entry highlight", "new file highlight"),
    "uploads.filename_template": ("upload filename template", "upload name template"),
    "uploads.subdir": ("upload subdir", "upload folder", "upload directory"),
    "uploads.show_suggestions": ("upload suggestions", "drop suggestions", "file action menu"),
    "uploads.suggestion_autorun": ("upload autorun", "drop autorun", "shell autorun"),
    "uploads.image_action_order": ("image action", "image actions", "image action order", "image paste actions", "image drop actions"),
    "uploads.custom_actions": ("custom upload actions", "custom drop actions", "file drop actions"),
    "uploads.max_bytes": ("file transfer size cap", "transfer size cap", "upload max bytes", "upload limit", "download limit", "max upload size", "max download size"),
    "share.ttl_seconds": ("share ttl", "share expiry", "share duration"),
    "share.max_viewers": ("share max viewers", "max viewers"),
    "share.read_only": ("share read only", "share write access"),
    "share.scheme": ("share scheme", "share protocol", "share https"),
    "share.view_fit": ("share view fit", "share fit", "mirror fit"),
    "performance.latency_refresh_ms": ("latency refresh", "ping refresh"),
    "performance.event_log_refresh_ms": ("event log refresh", "log refresh"),
    "performance.tabber_activity_refresh_ms": ("tabber refresh", "recent agents refresh", "activity refresh"),
    "performance.agent_status_pulse_period_ms": ("status ball pulse", "ball pulse period", "pulse frequency", "pulsating frequency", "red yellow green pulse"),
    "performance.server_event_poll_ms": ("server event poll", "visible file poll"),
    "performance.server_background_file_event_poll_ms": ("background file poll", "background file refresh"),
    "performance.server_directory_event_poll_ms": ("directory poll", "finder poll", "differ poll"),
    "performance.popover_show_delay_ms": ("popover show delay", "tooltip delay"),
    "performance.popover_hide_delay_ms": ("popover hide delay", "tooltip hide delay"),
    "performance.menu_hover_open_delay_ms": ("menu hover delay", "menu open delay"),
    "performance.tab_popover_show_delay_ms": ("tab popover delay", "tab hover delay"),
    "performance.tab_popover_follow_delay_ms": ("tab popover follow delay", "tab follow delay"),
    "performance.remote_resize_delay_ms": ("remote resize delay", "tmux resize delay"),
    "performance.auto_approve_interval_seconds": ("auto approve interval", "yolo worker interval", "approval poll interval"),
    "github.watched_prs": ("watched pr", "watched prs", "watched pull request", "watched pull requests", "pr watch list", "watch pr"),
    "terminal_editor.scrollback": ("scrollback", "terminal scrollback", "terminal history"),
    "terminal_editor.word_wrap": ("word wrap", "soft wrap", "wrap lines"),
    "terminal_editor.line_numbers": ("line numbers", "line number gutter"),
    "editor.autosave": ("autosave", "editor autosave"),
    "editor.autosave_delay_seconds": ("autosave delay", "editor autosave delay"),
    "editor.blame_all_lines": ("blame all lines", "inline blame all lines"),
    "yoagent.backend": ("yoagent backend", "yo!agent backend", "agent backend"),
    "yoagent.invocation": ("yoagent invocation", "yo!agent invocation"),
    "yoagent.system_prompt": ("yoagent system prompt", "yo!agent system prompt", "agent system prompt"),
    "yoagent.intro": ("yoagent intro", "yo!agent intro"),
    "yoagent.format": ("yoagent format", "yo!agent format", "answer format"),
    "yolo.rule_file_path": ("yolo rule file", "yolo rules path", "approval rule file"),
    "yolo.dry_run": ("yolo dry run", "approval dry run"),
    "yolo.prompt_source": ("yolo prompt source", "approval prompt source"),
}

BLOCKED_PATH_RE = re.compile(
    r"(?:^|/)(?:\.ssh|\.gnupg|\.aws)(?:/|$)|"
    r"(?:^|/)\.config/(?:gh|gitlab-token)(?:/|$)|"
    r"(?:^|/)\.cache/huggingface/token$|"
    r"(?:^|/)\.docker/config\.json$|"
    r"(?:^|/)\.ngc/config$|"
    r"(?:token|secret|password|api[_-]?key)",
    re.IGNORECASE,
)

PR_RE = re.compile(r"(?:https://github\.com/[^/\s]+/[^/\s]+/pull/\d+|[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+#\d+)")

WRITE_RE = re.compile(r"\b(?:set|change|switch|make|use|turn|enable|disable|add|remove|reset)\b", re.IGNORECASE)
READ_RE = re.compile(r"\b(?:what|where|which|show|list|explain|tell|current|default|value|values|setting|settings|preference|preferences|affect)\b", re.IGNORECASE)
CONFIRM_RE = re.compile(r"\b(?:confirm|confirmed|yes|go ahead|do it|apply anyway)\b", re.IGNORECASE)
YOAGENT_HIDDEN_SETTING_PATHS = {
    "appearance.editor_color_scheme",
    "general.default_sessions",
}
GENERIC_SETTING_KEY_ALIASES = {
    "backend",
    "color",
    "format",
    "invocation",
    "mode",
    "scheme",
    "source",
    "theme",
}


def normalize_text(value: Any) -> str:
    return " ".join(re.sub(r"[^a-z0-9_./#:-]+", " ", str(value or "").lower()).split())


def implicit_settings_write_question(question: str) -> bool:
    text = normalize_text(question)
    if re.search(r"\b(?:tab|tabs)\b", text) and (re.search(r"\b(?:wider|narrower|bigger|smaller|larger|width)\b", text) or re.search(r"\d", text)):
        return True
    if re.search(r"\b(?:ui|interface|app)\b", text) and re.search(r"\b(?:bigger|smaller|larger|font|size)\b", text):
        return True
    if "terminal" in text and re.search(r"\b(?:bigger|smaller|larger|font|size|light|dark|black|white)\b", text):
        return True
    if re.search(r"\b(?:no|quiet|silent|silence|disable|off)\b", text) and re.search(r"\b(?:notify|notification|notifications|alerts)\b", text):
        return True
    return False


def direct_setting_path_for_question(question: str, catalog: dict[str, dict[str, Any]]) -> str:
    text = normalize_text(question)
    path = ""
    if re.search(r"\b(?:tab|tabs)\b", text) and (re.search(r"\b(?:wider|narrower|bigger|smaller|larger|width)\b", text) or re.search(r"\d", text)):
        path = "appearance.tab_width"
    elif re.search(r"\b(?:ui|interface|app)\b", text) and re.search(r"\b(?:bigger|smaller|larger|font|size)\b", text):
        path = "appearance.ui_font_size"
    elif "terminal" in text and re.search(r"\b(?:font|size|bigger|smaller|larger)\b", text):
        path = "appearance.terminal_font_size"
    elif "terminal" in text and re.search(r"\b(?:light|dark|black|white)\b", text):
        path = "appearance.terminal_theme"
    elif re.search(r"\b(?:no|quiet|silent|silence|disable|off)\b", text) and re.search(r"\b(?:notify|notification|notifications|alerts)\b", text):
        path = "notifications.notify_transitions"
    return path if path in catalog else ""


def hidden_setting_message(question: str, locale: str = "en") -> str:
    text = normalize_text(question)
    if "appearance.editor_color_scheme" in text or "editor color scheme" in text or "editor scheme" in text:
        return yoagent_text(locale, "yoagent.operator.hiddenEditorScheme")
    if "general.default_sessions" in text or "default sessions" in text:
        return yoagent_text(locale, "yoagent.operator.hiddenDefaultSessions")
    return ""


def catalog_from_payload(payload: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("catalog"), dict):
        catalog = {str(path): item for path, item in payload["catalog"].items() if isinstance(item, dict)}
        return {path: item for path, item in catalog.items() if path not in YOAGENT_HIDDEN_SETTING_PATHS}
    settings = payload.get("settings") if isinstance(payload, dict) and isinstance(payload.get("settings"), dict) else default_settings()
    catalog = settings_catalog(settings)
    return {path: item for path, item in catalog.items() if path not in YOAGENT_HIDDEN_SETTING_PATHS}


def settings_from_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    settings = payload.get("settings") if isinstance(payload, dict) else None
    return settings if isinstance(settings, dict) else default_settings()


def nested_setting(settings: dict[str, Any], path: str) -> Any:
    section, key = path.split(".", 1)
    values = settings.get(section, {}) if isinstance(settings, dict) else {}
    defaults = default_settings()
    return values.get(key, defaults.get(section, {}).get(key)) if isinstance(values, dict) else defaults.get(section, {}).get(key)


def format_setting_value(value: Any, locale: str = "en") -> str:
    if isinstance(value, bool):
        return yoagent_text(locale, "notify.state.on" if value else "state.off")
    if isinstance(value, list):
        return ", ".join(f"`{item}`" for item in value) if value else yoagent_text(locale, "yoagent.prompt.none")
    return f"`{value}`"


def localized_setting_catalog_text(item: dict[str, Any], field: str, locale: str = "en") -> str:
    locale_keys = item.get("locale_keys") if isinstance(item.get("locale_keys"), dict) else {}
    key = str(locale_keys.get(field) or "").strip()
    if not key:
        return ""
    value = yoagent_text(locale, key)
    return "" if value == key else value


def setting_display_name(path: str, item: dict[str, Any] | None = None, locale: str = "en") -> str:
    localized = localized_setting_catalog_text(item or {}, "label", locale)
    label = str((item or {}).get("label") or "").strip()
    return localized or label or path.split(".", 1)[1].replace("_", " ")


def setting_location_text(item: dict[str, Any], locale: str = "en") -> str:
    gui = item.get("gui") if isinstance(item.get("gui"), dict) else {}
    section_key = str(gui.get("section_locale_key") or "").strip()
    section = yoagent_text(locale, section_key) if section_key else ""
    return yoagent_text(locale, "yoagent.operator.preferencesLocation", section=section) if section else yoagent_text(locale, "yoagent.operator.notInPreferences")


def setting_candidates(question: str, catalog: dict[str, dict[str, Any]]) -> list[tuple[int, str]]:
    text = normalize_text(question)
    candidates: list[tuple[int, str]] = []
    for path, item in catalog.items():
        aliases = list(SETTING_NAME_ALIASES.get(path, ()))
        key_terms = path.replace(".", " ").replace("_", " ")
        aliases.extend([path, key_terms])
        section, key = path.split(".", 1)
        key_alias = key.replace("_", " ")
        if key_alias not in GENERIC_SETTING_KEY_ALIASES:
            aliases.append(key_alias)
        score = 0
        for alias in aliases:
            normalized_alias = normalize_text(alias)
            if normalized_alias and normalized_alias in text:
                score = max(score, 100 + len(normalized_alias))
        if score == 0:
            words = [part for part in key_terms.split() if len(part) >= 3]
            if words and all(word in text for word in words):
                score = 40 + len(words)
        description = normalize_text(item.get("description") or "")
        if score == 0 and any(word in text for word in ["tab", "tabs"]) and "tab" in description:
            score = 25
        if score:
            candidates.append((score, path))
    candidates.sort(key=lambda item: (-item[0], item[1]))
    return candidates


def settings_for_topic(question: str, catalog: dict[str, dict[str, Any]]) -> list[str]:
    text = normalize_text(question)
    if "notification" in text or "notify" in text:
        return [path for path in catalog if path.startswith("notifications.") or path in {"updates.notify_level", "updates.check_enabled", "updates.check_interval_minutes"}]
    if "tab" in text or "tabs" in text:
        return [path for path in catalog if "tab" in path or "tab" in normalize_text(catalog[path].get("description") or "")]
    if "font" in text or "size" in text:
        return [path for path in catalog if "font_size" in path or path in {"appearance.tab_width", "file_explorer.image_preview_max_px"}]
    if "color" in text or "theme" in text or "cursor" in text:
        return [path for path in catalog if any(term in path for term in ["theme", "color", "cursor"])]
    if "upload" in text or "drop" in text or "paste" in text:
        return [path for path in catalog if path.startswith("uploads.")]
    if "share" in text:
        return [path for path in catalog if path.startswith("share.")]
    return []


def changed_settings_lines(payload: dict[str, Any], locale: str = "en") -> list[str]:
    settings = settings_from_payload(payload)
    defaults = payload.get("defaults") if isinstance(payload.get("defaults"), dict) else default_settings()
    catalog = catalog_from_payload(payload)
    lines: list[str] = []
    for path in sorted(catalog):
        current = nested_setting(settings, path)
        default = nested_setting(defaults, path)
        if current != default:
            lines.append(f"- `{path}`: {format_setting_value(default, locale)} -> {format_setting_value(current, locale)}")
    return lines


def setting_summary_line(path: str, item: dict[str, Any], locale: str = "en") -> str:
    parts = [
        yoagent_text(
            locale,
            "yoagent.operator.settingSummary",
            path=path,
            location=setting_location_text(item, locale),
            current=format_setting_value(item.get("current"), locale),
            default=format_setting_value(item.get("default"), locale),
        ),
    ]
    choices = item.get("choices") if isinstance(item.get("choices"), list) else []
    limits = item.get("limits") if isinstance(item.get("limits"), dict) else None
    units = str(item.get("units") or "")
    if choices:
        parts.append(yoagent_text(locale, "yoagent.operator.choices", choices=", ".join(f"`{choice}`" for choice in choices)))
    elif limits:
        unit_text = f" {units}" if units else ""
        parts.append(yoagent_text(locale, "yoagent.operator.range", minimum=limits.get("min"), maximum=limits.get("max"), units=unit_text))
    description = localized_setting_catalog_text(item, "description", locale)
    if description:
        parts.append(description)
    return " ".join(parts)


def answer_settings_read(question: str, payload: dict[str, Any], locale: str = "en") -> str:
    text = normalize_text(question)
    catalog = catalog_from_payload(payload)
    display_path = str(payload.get("display_path") or SETTINGS_DISPLAY_PATH)
    hidden_message = hidden_setting_message(question, locale)
    if hidden_message:
        return hidden_message
    if "where" in text and any(term in text for term in ["settings", "preferences", "config", "yaml"]):
        return yoagent_text(locale, "yoagent.operator.settingsLocation", path=display_path)
    if "changed" in text and ("default" in text or "defaults" in text):
        lines = changed_settings_lines(payload, locale)
        if not lines:
            return yoagent_text(locale, "yoagent.operator.noChangedDefaults", path=display_path)
        return "\n".join([yoagent_text(locale, "yoagent.operator.changedDefaults", path=display_path), *lines[:40]])
    topic_paths = settings_for_topic(question, catalog) if any(word in text for word in ["all", "affect", "settings", "preference", "preferences"]) else []
    candidates = setting_candidates(question, catalog)
    paths = topic_paths or [path for _score, path in candidates[:1]]
    if not paths and any(word in text for word in ["settings", "preferences"]):
        paths = ["appearance.theme", "appearance.active_color", "appearance.tab_width", "appearance.terminal_font_size", "updates.notify_level"]
    if not paths:
        return ""
    if len(paths) == 1:
        path = paths[0]
        item = catalog[path]
        return setting_summary_line(path, item, locale)
    lines = [setting_summary_line(path, catalog[path], locale) for path in paths[:12]]
    extra = len(paths) - len(lines)
    if extra > 0:
        lines.append(yoagent_text(locale, "yoagent.operator.moreSettings", count=extra))
    return "\n".join(lines)


def coerce_bool_from_question(question: str, current: bool) -> bool | None:
    text = normalize_text(question)
    if any(phrase in text for phrase in ["turn on", "enable", "enabled", "true", "yes", "on"]):
        return True
    if any(phrase in text for phrase in ["turn off", "disable", "disabled", "false", "no", "off"]):
        return False
    if "toggle" in text:
        return not current
    return None


def numeric_value_from_question(question: str, item: dict[str, Any], current: Any, locale: str = "en") -> tuple[int | float | None, str]:
    text = normalize_text(question)
    units = str(item.get("units") or "")
    limits = item.get("limits") if isinstance(item.get("limits"), dict) else {}
    step = 1
    if units == "milliseconds":
        step = 100
    elif item.get("path") == "appearance.tab_width":
        step = 20
    if any(word in text for word in ["bigger", "larger", "increase", "wider", "slower", "longer", "more"]):
        try:
            number = float(current) + step
        except (TypeError, ValueError):
            return None, ""
        return clamp_numeric_setting_value(number, item, locale)
    if any(word in text for word in ["smaller", "decrease", "narrower", "faster", "shorter", "less"]):
        try:
            number = float(current) - step
        except (TypeError, ValueError):
            return None, ""
        return clamp_numeric_setting_value(number, item, locale)
    match = re.search(r"(-?\d+(?:\.\d+)?)", question)
    if not match:
        return None, ""
    number = float(match.group(1))
    lower_context = question[max(0, match.start() - 12): match.end() + 16].lower()
    if units == "milliseconds" and re.search(r"\b(?:s|sec|secs|second|seconds)\b", lower_context):
        number *= 1000
    if units == "seconds" and re.search(r"\b(?:ms|millisecond|milliseconds)\b", lower_context):
        number /= 1000
    if item.get("path") == "uploads.max_bytes" and re.search(r"\b(?:mb|mib|megabyte|megabytes)\b", lower_context):
        number *= 1024 * 1024
    return clamp_numeric_setting_value(number, item, locale)


def clamp_numeric_setting_value(number: float, item: dict[str, Any], locale: str = "en") -> tuple[int | float, str]:
    limits = item.get("limits") if isinstance(item.get("limits"), dict) else {}
    units = str(item.get("units") or "")
    requested = float(number)
    if item.get("type") == "integer":
        number = int(round(number))
    note = ""
    if limits:
        lower = float(limits.get("min"))
        upper = float(limits.get("max"))
        clamped = max(lower, min(upper, float(number)))
        if clamped != float(number):
            unit_text = f" {units}" if units else ""
            note = yoagent_text(locale, "yoagent.operator.clamped", minimum=limits.get("min"), maximum=limits.get("max"), units=unit_text)
        number = clamped
    value = int(number) if item.get("type") == "integer" else round(float(number), 3)
    if note and value == requested:
        note = ""
    return value, note


def choice_value_from_text(value_text: str, item: dict[str, Any]) -> str:
    text = normalize_text(value_text)
    raw_text = str(value_text or "").casefold()
    choices = [str(choice) for choice in item.get("choices") or []]
    aliases = item.get("aliases") if isinstance(item.get("aliases"), dict) else {}
    for alias, canonical in aliases.items():
        normalized_alias = normalize_text(alias)
        raw_alias = str(alias or "").casefold()
        if normalized_alias and normalized_alias in text:
            return str(canonical)
        if not normalized_alias and raw_alias and raw_alias in raw_text:
            return str(canonical)
    choice_aliases = {
        "on": "true",
        "off": "false",
        "read only": "true",
        "readonly": "true",
        "write": "false",
        "writable": "false",
        "auto": "system",
        "default": "system",
        "white": "light",
        "bright": "light",
        "black": "dark",
        "12 hour": "12",
        "24 hour": "24",
    }
    for choice in choices:
        variants = {choice, choice.replace("-", " "), choice.replace("_", " ")}
        for alias, canonical in choice_aliases.items():
            if canonical == choice:
                variants.add(alias)
        if any(normalize_text(variant) and normalize_text(variant) in text for variant in variants):
            return choice
    return ""


def destination_value_text(question: str) -> str:
    for pattern in (
        r"\bfrom\s+\S+\s+to\s+(.+)$",
        r"\bto\s+(.+)$",
        r"\bas\s+(.+)$",
        r"\buse\s+(.+)$",
    ):
        match = re.search(pattern, question, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return ""


def choice_value_from_question(question: str, item: dict[str, Any]) -> str:
    destination = destination_value_text(question)
    if destination:
        value = choice_value_from_text(destination, item)
        if value:
            return value
    return choice_value_from_text(question, item)


def unsafe_config_path_value(path: str, value: str) -> bool:
    text = str(value or "").strip()
    if path != "yolo.rule_file_path":
        return False
    if not text.startswith("/"):
        return False
    return not (text.startswith("/home/") and "/.config/yolomux/" in text)


def string_value_from_question(question: str, path: str) -> str:
    match = re.search(r"\b(?:to|as|use)\s+(.+)$", question, flags=re.IGNORECASE)
    value = match.group(1).strip(" `\"'") if match else ""
    if path == "uploads.subdir":
        value = value.split()[0] if value else ""
    return value


def quoted_or_path_like_item(question: str) -> str:
    quoted = re.search(r"`([^`]+)`|['\"]([^'\"]+)['\"]", question)
    item = (quoted.group(1) or quoted.group(2)).strip() if quoted else ""
    if item:
        return item
    path_match = re.search(r"(?:~|/|\.)[^\s,;]+", question)
    return path_match.group(0).strip() if path_match else ""


def notification_transition_item(question: str, locale: str = "en") -> tuple[str, str]:
    text = normalize_text(question)
    aliases = {
        "needs input": "needs-input",
        "needs approval": "needs-approval",
        "yolo approval": "yolo-approval",
        "tests running": "tests-running",
        "ready review": "ready-review",
        "pr merged": "pr-merged",
        "pr ci failing": "pr-ci-failing",
        "ci failing": "pr-ci-failing",
        "pr review": "pr-review",
    }
    for alias, canonical in aliases.items():
        if alias in text:
            return canonical, ""
    for item in sorted(NOTIFY_TRANSITION_KEYS, key=len, reverse=True):
        if normalize_text(item) in text:
            return item, ""
    return "", yoagent_text(locale, "yoagent.operator.notificationTransitionRequired")


def image_action_order_item(question: str, locale: str = "en") -> tuple[str, str]:
    raw = quoted_or_path_like_item(question)
    text = normalize_text(question)
    if not raw:
        if "ocr" in text or "extract text" in text:
            raw = "img-ocr"
        elif "diagnose" in text or "error" in text:
            raw = "img-error"
        elif "describe" in text:
            raw = "img-describe"
        elif "info" in text or "file info" in text:
            raw = "info"
    canonical = canonical_image_action_order_item(raw)
    if not canonical:
        return "", yoagent_text(locale, "yoagent.operator.imageActionRequired")
    return canonical, ""


def list_value_from_question(question: str, path: str, current: list[Any], operation: str, locale: str = "en") -> tuple[list[str] | None, str]:
    values = [str(item) for item in current if str(item).strip()]
    if path == "github.watched_prs":
        match = PR_RE.search(question)
        if not match:
            return None, yoagent_text(locale, "yoagent.operator.prRequired")
        parsed = parse_pull_request_ref(match.group(0))
        if not parsed:
            return None, yoagent_text(locale, "yoagent.operator.prInvalid")
        item = str(parsed["ref"])
    elif path == "notifications.notify_transitions":
        item, error = notification_transition_item(question, locale)
        if error:
            return None, error
    elif path == "uploads.image_action_order":
        item, error = image_action_order_item(question, locale)
        if error:
            return None, error
    else:
        item = quoted_or_path_like_item(question)
    if not item:
        return None, yoagent_text(locale, "yoagent.operator.exactListItem")
    if BLOCKED_PATH_RE.search(item):
        return None, yoagent_text(locale, "yoagent.operator.credentialSensitive")
    if operation == "remove":
        next_values = [value for value in values if value != item]
        return next_values, "" if len(next_values) != len(values) else yoagent_text(locale, "yoagent.operator.itemNotPresent", item=item, path=path)
    if item in values:
        return values, yoagent_text(locale, "yoagent.operator.itemAlreadyPresent", item=item, path=path)
    values.append(item)
    return values, ""


def setting_value_from_question(question: str, item: dict[str, Any], operation: str, locale: str = "en") -> tuple[Any, str]:
    path = str(item.get("path") or "")
    current = item.get("current")
    setting_type = str(item.get("type") or "")
    if operation == "reset":
        return item.get("default"), ""
    if setting_type == "boolean":
        value = coerce_bool_from_question(question, bool(current))
        return (value, "") if value is not None else (None, yoagent_text(locale, "yoagent.operator.onOffRequired", path=path))
    if setting_type in {"integer", "number"}:
        value, note = numeric_value_from_question(question, item, current, locale)
        return (value, note) if value is not None else (None, yoagent_text(locale, "yoagent.operator.numberRequired", path=path))
    if item.get("choices"):
        value = choice_value_from_question(question, item)
        choices = ", ".join(f"`{choice}`" for choice in item.get("choices") or [])
        return (value, "") if value else (None, yoagent_text(locale, "yoagent.operator.choiceRequired", choices=choices))
    if setting_type == "list":
        if path == "notifications.notify_transitions" and operation == "set":
            text = normalize_text(question)
            if re.search(r"\b(?:no|quiet|silent|silence|disable|off)\b", text) and re.search(r"\b(?:notify|notification|notifications|alerts)\b", text):
                return [], ""
        if operation not in {"add", "remove"}:
            return None, yoagent_text(locale, "yoagent.operator.listOperationRequired", path=path)
        return list_value_from_question(question, path, current if isinstance(current, list) else [], operation, locale)
    value = string_value_from_question(question, path)
    if value == "" and item.get("empty_allowed"):
        return "", ""
    if not value:
        return None, yoagent_text(locale, "yoagent.operator.newTextRequired", path=path)
    if item.get("sensitivity") in {"path", "path-list"} and BLOCKED_PATH_RE.search(value):
            return None, yoagent_text(locale, "yoagent.operator.credentialSensitive")
    if unsafe_config_path_value(path, value):
        return None, yoagent_text(locale, "yoagent.operator.unsafeConfigPath", path=path)
    return value, ""


def patch_for_setting(path: str, value: Any) -> dict[str, dict[str, Any]]:
    section, key = path.split(".", 1)
    return {section: {key: value}}


def changed_setting_answer(path: str, item: dict[str, Any], before: Any, after: Any, note: str = "", coerced: list[str] | None = None, locale: str = "en") -> str:
    live_apply = str(item.get("live_apply") or "live")
    lines = [
        yoagent_text(
            locale,
            "yoagent.operator.updatedPreference",
            path=path,
            before=format_setting_value(before, locale),
            after=format_setting_value(after, locale),
            location=setting_location_text(item, locale),
            live_apply=live_apply,
        ),
    ]
    if note:
        lines.extend(["", note.strip()])
    if coerced:
        lines.extend(["", yoagent_text(locale, "yoagent.operator.coerced", keys=", ".join(f"`{key}`" for key in coerced))])
    return "\n".join(lines)


def parse_settings_write(question: str, payload: dict[str, Any], locale: str = "en") -> dict[str, Any] | None:
    if READ_RE.search(question) and not WRITE_RE.search(question):
        return None
    if not WRITE_RE.search(question) and not implicit_settings_write_question(question):
        return None
    text = normalize_text(question)
    hidden_message = hidden_setting_message(question, locale)
    if hidden_message:
        return {"type": "settings_clarify", "answer": hidden_message}
    if "reset" in text and any(term in text for term in ["all", "everything", "preferences", "settings"]):
        return {"type": "settings_clarify", "answer": yoagent_text(locale, "yoagent.operator.resetBroad")}
    catalog = catalog_from_payload(payload)
    direct_path = direct_setting_path_for_question(question, catalog)
    candidates = [(1000, direct_path)] if direct_path else setting_candidates(question, catalog)
    if not candidates:
        return None
    top_score = candidates[0][0]
    top_paths = [path for score, path in candidates if score == top_score][:4]
    if len(top_paths) > 1 and not any(path in normalize_text(question) for path in top_paths):
        labels = ", ".join(f"`{path}`" for path in top_paths)
        return {"type": "settings_clarify", "answer": yoagent_text(locale, "yoagent.operator.whichSetting", settings=labels)}
    path = top_paths[0]
    item = catalog[path]
    if "reset" in text:
        operation = "reset"
    elif "remove" in text or "delete" in text:
        operation = "remove"
    elif "add" in text:
        operation = "add"
    else:
        operation = "set"
    value, error = setting_value_from_question(question, item, operation, locale)
    if error and value is None:
        return {"type": "settings_clarify", "answer": error}
    requires_confirmation = bool(item.get("requires_confirmation")) and not CONFIRM_RE.search(question)
    return {
        "type": "settings_write",
        "path": path,
        "value": value,
        "patch": patch_for_setting(path, value),
        "operation": operation,
        "note": error,
        "requires_confirmation": requires_confirmation,
        "item": item,
    }


def parse_settings_read(question: str, payload: dict[str, Any], locale: str = "en") -> dict[str, Any] | None:
    text = normalize_text(question)
    if not READ_RE.search(question):
        return None
    if not any(term in text for term in [
        "setting", "settings", "preference", "preferences", "theme", "color", "font", "cursor", "tab", "notify",
        "notification", "upload", "share", "yolo", "yoagent", "yo agent", "finder", "differ", "settings.yaml",
        "config", "default",
    ]):
        return None
    answer = answer_settings_read(question, payload, locale)
    return {"type": "settings_read", "answer": answer} if answer else None


# Maps a structured BackendAvailability reason to the locale key + substitution params the UI should
# render instead of the generic det.noBackend string. The keys live in static_src/locales/en.json.
BACKEND_REASON_LOCALE_KEYS = {
    BACKEND_REASON_NO_PROVIDER: "det.noBackend.noProvider",
    BACKEND_REASON_MODULE_MISSING: "det.noBackend.moduleMissing",
    BACKEND_REASON_NO_CREDENTIALS: "det.noBackend.noCredentials",
}


def backend_no_backend_notice(availability: BackendAvailability) -> dict[str, Any]:
    """Translate a structured backend diagnostic into a UI notice.

    Returns the specific locale key plus params (provider / module / command) so the YO!agent chat can
    say WHICH precondition failed and point at the exact fix, rather than the generic det.noBackend
    "set or log in a backend" message. When the backend IS available, falls back to the generic key so
    callers still have something to show for an unexpected empty answer.
    """
    if availability.available or availability.reason == BACKEND_REASON_AVAILABLE:
        return {"reason": availability.reason, "locale_key": "det.noBackend", "params": {}}
    locale_key = BACKEND_REASON_LOCALE_KEYS.get(availability.reason, "det.noBackend")
    params: dict[str, str] = {}
    if availability.reason == BACKEND_REASON_MODULE_MISSING:
        params = {
            "provider": availability.provider or availability.backend,
            "module": availability.sdk_module,
        }
    elif availability.reason == BACKEND_REASON_NO_CREDENTIALS:
        params = {
            "provider": availability.provider or availability.backend,
            "command": availability.login_command,
        }
    return {
        "reason": availability.reason,
        "locale_key": locale_key,
        "params": params,
        "backend": availability.backend,
        "detail": availability.detail,
    }


def product_capability_registry() -> list[dict[str, Any]]:
    return [
        {
            "key": "preferences",
            "name": "Preferences",
            "read": True,
            "write": True,
            "read_action": "explain current/default/choices/ranges",
            "write_action": "save validated settings patch",
            "auth": "readonly for reads, admin for writes",
            "locale_keys": {"name": "common.preferences"},
            "backing": "settings_catalog + TmuxWebtermApp.save_settings",
            "setting_keys": ["appearance.theme", "appearance.tab_width", "updates.notify_level", "uploads.subdir"],
            "examples": ["what is my tab width?", "set theme to light", "change update notify level to patch"],
        },
        {
            "key": "panesTabs",
            "name": "Panes and tabs",
            "read": True,
            "write": True,
            "read_action": "summarize layout/tabs",
            "write_action": "route normal GUI tab actions",
            "auth": "admin for writes",
            "locale_keys": {"auth": "yoagent.capability.auth.adminForWrites"},
            "backing": "layout state helpers and tab action handlers",
            "setting_keys": ["appearance.tab_width", "appearance.max_tabs_per_pane"],
            "examples": ["what tabs are open?", "open YO!agent on the right pane"],
        },
        {
            "key": "finderDifferTabber",
            "name": "Finder, Differ, and Tabber",
            "read": True,
            "write": True,
            "read_action": "use cached session files/activity",
            "write_action": "use Finder/Differ open/search APIs",
            "auth": "admin for writes",
            "locale_keys": {"auth": "yoagent.capability.auth.adminForWrites"},
            "backing": "/api/session-files, /api/activity-summary, filesystem helpers",
            "setting_keys": ["file_explorer.root_mode", "file_explorer.indexed_dirs"],
            "examples": ["where is README.md?", "show recent agents", "open changed files"],
        },
        {
            "key": "orchestration",
            "name": "Agent orchestration",
            "read": True,
            "write": True,
            "read_action": "inspect activity/transcripts/jobs",
            "write_action": "server-verified prompt preview/send/watch",
            "auth": "admin",
            "backing": "yoagent action/job helpers and transport registry",
            "setting_keys": ["yoagent.backend", "yoagent.invocation"],
            "examples": ["ask session 1 what changed, then ask session 2 if it is correct", "notify me when all sessions are idle"],
        },
        {
            "key": "skills",
            "name": "YO!skills",
            "read": True,
            "write": True,
            "read_action": "list/read built-in and user skill files",
            "write_action": "upsert/delete user-local skill/context files",
            "auth": "admin for user-local files",
            "backing": "/api/yoagent/skill-files",
            "setting_keys": ["yoagent.system_prompt", "yoagent.intro", "yoagent.format"],
            "examples": ["list my YO!skills", "create a skill local-status"],
        },
        {
            "key": "uploads",
            "name": "Uploads and file actions",
            "read": True,
            "write": True,
            "read_action": "explain upload/drop action settings",
            "write_action": "save validated upload Preferences",
            "auth": "admin for writes",
            "locale_keys": {"auth": "yoagent.capability.auth.adminForWrites"},
            "backing": "settings catalog + drop-action registry",
            "setting_keys": ["uploads.image_action_order", "uploads.custom_actions", "uploads.subdir"],
            "examples": ["what are my image paste actions?", "set upload subdir to .uploads"],
        },
        {
            "key": "share",
            "name": "YO!share",
            "read": True,
            "write": True,
            "read_action": "explain share defaults and active shares",
            "write_action": "route through share creation/management helpers",
            "auth": "admin for shares",
            "locale_keys": {"name": "brand.share"},
            "backing": "share APIs and share.* Preferences",
            "setting_keys": ["share.ttl_seconds", "share.max_viewers", "share.read_only", "share.scheme"],
            "examples": ["what are my share defaults?", "make share links read-only"],
        },
        {
            "key": "recentWork",
            "name": "PR and recent-work state",
            "read": True,
            "write": False,
            "read_action": "summarize cached activity, watched PRs, transcript metadata",
            "write_action": "",
            "auth": "readonly",
            "locale_keys": {"write": "common.readOnly"},
            "backing": "/api/activity-summary and watched PR metadata",
            "setting_keys": ["github.watched_prs", "notifications.notify_transitions"],
            "examples": ["what did I last work on?", "what PR was that?"],
        },
    ]


def product_capability_locale_key(item: dict[str, Any], field: str) -> str:
    locale_keys = item.get("locale_keys") if isinstance(item.get("locale_keys"), dict) else {}
    return str(locale_keys.get(field) or f"yoagent.capability.{item['key']}.{field}")


def product_capabilities_answer(locale: str = "en") -> str:
    lines = [yoagent_text(locale, "yoagent.capabilities.intro")]
    for item in product_capability_registry():
        key = str(item["key"])
        examples = "; ".join(f"`{yoagent_text(locale, f'yoagent.capability.{key}.example{index}')}`" for index in range(1, 3))
        backing_value = yoagent_text(locale, f"yoagent.capability.{key}.backing") if item.get("backing") else ""
        backing = yoagent_text(locale, "yoagent.capabilities.backing", backing=backing_value) if backing_value else ""
        lines.append(yoagent_text(
            locale,
            "yoagent.capabilities.item",
            name=yoagent_text(locale, product_capability_locale_key(item, "name")),
            auth=yoagent_text(locale, product_capability_locale_key(item, "auth")),
            read=yoagent_text(locale, product_capability_locale_key(item, "read")),
            write=yoagent_text(locale, product_capability_locale_key(item, "write")),
            backing=backing,
            examples=examples,
        ))
    return "\n".join(lines)


def sorted_session_summaries(activity_payload: dict[str, Any]) -> list[dict[str, Any]]:
    sessions = activity_payload.get("sessions") if isinstance(activity_payload, dict) else {}
    summaries = []
    if isinstance(sessions, dict):
        for key, summary in sessions.items():
            if isinstance(summary, dict):
                summaries.append(summary if summary.get("session") else {**summary, "session": key})
    summaries.sort(key=lambda item: -float(item.get("last_activity_ts") or 0.0))
    return summaries


def answer_product_state(question: str, activity_payload: dict[str, Any], locale: str = "en") -> str:
    text = normalize_text(question)
    if ("what can" in text and ("yoagent" in text or "yo agent" in text or "yolomux" in text)) or "what can i do from here" in text:
        return product_capabilities_answer(locale)
    if "where" in text and ("skills" in text or "context" in text):
        return yoagent_text(locale, "yoagent.operator.skillLocations")
    summaries = sorted_session_summaries(activity_payload)
    if any(phrase in text for phrase in ["last worked", "last work", "what did i work", "what was i working"]):
        if not summaries:
            return yoagent_text(locale, "yoagent.operator.noRecentActivity")
        item = summaries[0]
        repos = ", ".join(f"`{repo}`" for repo in item.get("repos") or []) or f"`{yoagent_text(locale, 'common.unknown')}`"
        work = str(item.get("work") or item.get("goal") or item.get("status_text") or yoagent_text(locale, "common.unknown"))
        last = str(item.get("last_activity_text") or yoagent_text(locale, "common.unknown"))
        return yoagent_text(locale, "yoagent.operator.lastWork", session=item.get("session"), repos=repos, work=work, last=last)
    if "what pr" in text or "which pr" in text or "pull request" in text:
        rows = [item for item in summaries if item.get("pr_number") or "PR #" in str(item.get("status_text") or "")]
        if not rows:
            return yoagent_text(locale, "yoagent.operator.prNotCached")
        return "\n".join(yoagent_text(locale, "yoagent.operator.prRow", session=item.get("session"), number=item.get("pr_number") or "?", detail=item.get("status_text") or item.get("work") or "") for item in rows[:8])
    if "which sessions" in text or "sessions are" in text:
        if not summaries:
            return yoagent_text(locale, "yoagent.operator.noCachedActivity")
        lines = [yoagent_text(locale, "yoagent.operator.sessionTableHeader"), "| --- | --- | --- |"]
        for item in summaries:
            state = str(item.get("activity_label") or item.get("state", {}).get("key") or yoagent_text(locale, "common.unknown"))
            work = str(item.get("work") or item.get("goal") or item.get("status_text") or "")
            lines.append(f"| `{item.get('session')}` | {state} | {work} |")
        return "\n".join(lines)
    if ("files" in text and ("touch" in text or "changed" in text or "worked" in text)) or "what changed" in text or "changed in this repo" in text:
        lines: list[str] = []
        for item in summaries:
            for file_line in item.get("file_lines") or []:
                session_label = yoagent_text(locale, "common.tmuxSession", label=f"`{item.get('session')}`")
                lines.append(f"- {session_label}: {file_line}")
        if lines:
            return "\n".join([yoagent_text(locale, "yoagent.operator.cachedChangedFiles"), *lines[:20]])
        agents = activity_payload.get("agents") if isinstance(activity_payload, dict) else []
        for agent in agents if isinstance(agents, list) else []:
            for path in agent.get("recent_paths") or []:
                if isinstance(path, dict) and path.get("path"):
                    session_label = yoagent_text(locale, "common.tmuxSession", label=f"`{agent.get('session')}`")
                    lines.append(f"- {session_label}: `{path.get('path')}`")
        return "\n".join([yoagent_text(locale, "yoagent.operator.cachedRecentPaths"), *lines[:20]]) if lines else yoagent_text(locale, "yoagent.operator.noCachedChangedFiles")
    if "why" in text and ("session" in text or "tab" in text):
        session_match = re.search(r"\b(?:session|tab)\s+([A-Za-z0-9_.:-]+)\b", question, flags=re.IGNORECASE)
        wanted = session_match.group(1) if session_match else ""
        rows = [item for item in summaries if not wanted or str(item.get("session") or "") == wanted]
        if not rows:
            return yoagent_text(locale, "yoagent.operator.noSessionDetail")
        item = rows[0]
        state = str(item.get("activity_label") or item.get("state", {}).get("key") or yoagent_text(locale, "common.unknown"))
        work = str(item.get("work") or item.get("goal") or item.get("status_text") or yoagent_text(locale, "common.unknown"))
        blockers = item.get("blockers") if isinstance(item.get("blockers"), list) else []
        blocker_text = yoagent_text(locale, "yoagent.operator.blockers", blockers=", ".join(str(blocker) for blocker in blockers[:3])) if blockers else ""
        return yoagent_text(locale, "yoagent.operator.currentDetail", session=item.get("session"), state=state, work=work, blockers=blocker_text)
    if "where is my" in text or ("where" in text and "file" in text):
        term = text.split("where is my", 1)[-1].replace("file", "").strip() if "where is my" in text else ""
        matches: list[str] = []
        for item in summaries:
            for file_line in item.get("file_lines") or []:
                if not term or term in normalize_text(file_line):
                    session_label = yoagent_text(locale, "common.tmuxSession", label=f"`{item.get('session')}`")
                    matches.append(f"- {session_label}: {file_line}")
        return "\n".join([yoagent_text(locale, "yoagent.operator.possibleFileMatches"), *matches[:12]]) if matches else yoagent_text(locale, "yoagent.operator.fileNotCached")
    return ""


def product_state_needs_activity(question: str) -> bool:
    text = normalize_text(question)
    if any(phrase in text for phrase in ["last worked", "last work", "what did i work", "what was i working"]):
        return True
    if "what pr" in text or "which pr" in text or "pull request" in text:
        return True
    if "which sessions" in text or "sessions are" in text:
        return True
    if ("files" in text and ("touch" in text or "changed" in text or "worked" in text)) or "what changed" in text or "changed in this repo" in text:
        return True
    if "why" in text and ("session" in text or "tab" in text):
        return True
    if "where is my" in text or ("where" in text and "file" in text):
        return True
    return False


def yoagent_operator_response(
    question: str,
    settings_payload_data: dict[str, Any],
    activity_payload: dict[str, Any],
    access_role: str,
    save_settings_callback: Callable[[dict[str, Any]], dict[str, Any]],
    locale: str = "en",
) -> dict[str, Any] | None:
    started = time.monotonic()
    write_intent = parse_settings_write(question, settings_payload_data, locale)
    if write_intent:
        answer = str(write_intent.get("answer") or "")
        if write_intent["type"] == "settings_clarify":
            return deterministic_response(answer, started)
        if access_role != "admin":
            path = str(write_intent.get("path") or "")
            return deterministic_response(yoagent_text(locale, "yoagent.operator.readonlyWrite", path=path), started)
        if write_intent.get("requires_confirmation"):
            path = str(write_intent.get("path") or "")
            return deterministic_response(yoagent_text(locale, "yoagent.operator.confirmRisk", path=path), started)
        path = str(write_intent["path"])
        before = nested_setting(settings_from_payload(settings_payload_data), path)
        result = save_settings_callback(write_intent["patch"])
        after = nested_setting(settings_from_payload(result), path)
        item = catalog_from_payload(result).get(path, write_intent.get("item") or {})
        note = f" {write_intent.get('note')}" if write_intent.get("note") else ""
        coerced = result.get("coerced") if isinstance(result.get("coerced"), list) else []
        answer = changed_setting_answer(path, item, before, after, note=note, coerced=coerced, locale=locale)
        return deterministic_response(answer, started, changed_settings=[{"path": path, "before": before, "after": after}])
    read_intent = parse_settings_read(question, settings_payload_data, locale)
    if read_intent:
        return deterministic_response(str(read_intent["answer"]), started)
    product_answer = answer_product_state(question, activity_payload, locale)
    if product_answer:
        return deterministic_response(product_answer, started)
    return None


def deterministic_response(answer: str, started: float, changed_settings: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "answer": answer,
        "actions": [],
        "backend": "yolomux",
        "backend_used": "yolomux",
        "fallback": False,
        "fallback_reason": "",
        "cli": {},
        "deterministic": True,
        "timing": {"ttfr_ms": round((time.monotonic() - started) * 1000, 3)},
        "changed_settings": changed_settings or [],
    }
