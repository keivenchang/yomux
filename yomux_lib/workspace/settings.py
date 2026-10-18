# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""User preferences for YOmux."""

from __future__ import annotations

import copy
import json
import math
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import yaml

from ..infra.atomic_file import atomic_write_text
from ..infra.common import CONFIG_DIR
from ..infra.common import DEFAULT_UPLOAD_FILENAME_TEMPLATE
from ..infra.common import UPDATE_NOTIFY_LEVELS
from ..infra.common import UPLOAD_MAX_BYTES
from ..infra.shared_config_lock import shared_config_lock
from ..prune_policy import DEFAULT_PRUNE_LOCAL_TIME
from ..prune_policy import PRUNE_LOCAL_TIME_CHOICES
from .locales import LANGUAGE_PREFERENCES
from .locales import LANGUAGE_VALUE_ALIASES
from .locales import SYSTEM_LOCALE_PREFERENCE
from .locales import language_preference_description
from tools.product_environment import product_env, product_env_name


_SETTINGS_SCHEMA = json.loads((Path(__file__).resolve().parents[2] / "assets" / "runtime" / "settings-schema.json").read_text(encoding="utf-8"))
_SETTINGS_SCHEMA["SETTING_CHOICES"]["general.language"] = sorted(LANGUAGE_PREFERENCES)
_SETTINGS_SCHEMA["SETTING_VALUE_ALIASES"]["general.language"] = dict(LANGUAGE_VALUE_ALIASES)
_SETTINGS_SCHEMA["SETTING_COMMENTS"]["general.language"] = language_preference_description()

SETTINGS_PATH = CONFIG_DIR / "settings.yaml"
SETTINGS_DISPLAY_PATH = "~/.config/yomux/settings.yaml"
_SETTINGS_PAYLOAD_CACHE: dict[Path, tuple[int, int, dict[str, Any]]] = {}
_PRICING_PROFILE_HISTORY_KEYS = {
    "openai": "_openai_pricing_profile_history",
    "anthropic": "_anthropic_pricing_profile_history",
}
UI_COLOR_CHOICES = tuple(_SETTINGS_SCHEMA["SETTING_PAYLOAD_CHOICE_ORDER"]["appearance.active_color"])
HOVER_SHOW_DELAY_MS = _SETTINGS_SCHEMA["DEFAULT_SETTINGS"]["performance"]["popover_show_delay_ms"]
HOVER_HIDE_DELAY_MS = _SETTINGS_SCHEMA["DEFAULT_SETTINGS"]["performance"]["popover_hide_delay_ms"]
RETIRED_HOVER_TIMING_KEYS = tuple(_SETTINGS_SCHEMA["RETIRED_HOVER_TIMING_KEYS"])
DEFAULT_CURSOR_COLOR = _SETTINGS_SCHEMA["DEFAULT_SETTINGS"]["appearance"]["editor_cursor_color"]
SEPARATOR_COLOR_CHOICES = tuple(_SETTINGS_SCHEMA["SETTING_PAYLOAD_CHOICE_ORDER"]["appearance.separator_color"])
NEON_CURSOR_COLOR_CHOICES = tuple(value for value in _SETTINGS_SCHEMA["SETTING_PAYLOAD_CHOICE_ORDER"]["appearance.editor_cursor_color"] if value not in (*UI_COLOR_CHOICES, "theme"))
CURSOR_COLOR_CHOICES = tuple(_SETTINGS_SCHEMA["SETTING_PAYLOAD_CHOICE_ORDER"]["appearance.editor_cursor_color"])
POPULAR_IDE_DARK_SCHEME = "popular-ide-dark-plus"
POPULAR_IDE_LIGHT_SCHEME = "popular-ide-light-plus"
LEGACY_EDITOR_SCHEME_PREFIX = "".join(("vs", "code"))
YOAGENT_CLAUDE_MODEL_CHOICES = tuple(_SETTINGS_SCHEMA["SETTING_PAYLOAD_CHOICE_ORDER"]["yoagent.claude_model"])
YOAGENT_CODEX_EFFORT_CHOICES = tuple(next(iter(_SETTINGS_SCHEMA["YOAGENT_CODEX_MODEL_CATALOG"].values()))["effort_options"])
YOAGENT_CODEX_MODEL_CATALOG = {model: {**spec, "effort_options": tuple(spec["effort_options"])} for model, spec in _SETTINGS_SCHEMA["YOAGENT_CODEX_MODEL_CATALOG"].items()}
YOAGENT_CODEX_MODEL_CHOICES = tuple(YOAGENT_CODEX_MODEL_CATALOG)
YOAGENT_DEFAULT_CLAUDE_MODEL = _SETTINGS_SCHEMA["DEFAULT_SETTINGS"]["yoagent"]["claude_model"]
SUMMARY_CODEX_SERVICE_TIER_CHOICES = tuple(_SETTINGS_SCHEMA["SETTING_PAYLOAD_CHOICE_ORDER"]["summary.codex_service_tier"])
SUMMARY_DEFAULT_LOOKBACK_SECONDS = _SETTINGS_SCHEMA["DEFAULT_SETTINGS"]["summary"]["lookback_seconds"]
SUMMARY_DEFAULT_CODEX_TIMEOUT_SECONDS = _SETTINGS_SCHEMA["DEFAULT_SETTINGS"]["summary"]["timeout_seconds"]


def env_choice_default(name: str, default: str, choices: tuple[str, ...]) -> str:
    value = str(product_env(name, "") or "").strip()
    return value if value in choices else default


SUMMARY_DEFAULT_CODEX_MODEL = env_choice_default(
    'SUMMARY_MODEL',
    _SETTINGS_SCHEMA["DEFAULT_SETTINGS"]["summary"]["codex_model"],
    YOAGENT_CODEX_MODEL_CHOICES,
)
SUMMARY_DEFAULT_CODEX_EFFORT = env_choice_default('SUMMARY_EFFORT', "low", YOAGENT_CODEX_EFFORT_CHOICES)
SUMMARY_DEFAULT_CODEX_SERVICE_TIER = env_choice_default('SUMMARY_SERVICE_TIER', "fast", SUMMARY_CODEX_SERVICE_TIER_CHOICES)
DEFAULT_INDEX_EXCLUDE_DIR_NAMES = tuple(_SETTINGS_SCHEMA["DEFAULT_SETTINGS"]["file_explorer"]["index_exclude_dir_names"])
DEFAULT_INDEX_EXCLUDE_PATHS = tuple(_SETTINGS_SCHEMA["DEFAULT_SETTINGS"]["file_explorer"]["index_exclude_paths"])
IMAGE_DROP_ACTION_ORDER_SPECS = tuple({**spec, "aliases": tuple(spec["aliases"])} for spec in _SETTINGS_SCHEMA["IMAGE_DROP_ACTION_ORDER_SPECS"])
DEFAULT_IMAGE_DROP_ACTION_ORDER: tuple[str, ...] = tuple(str(spec["canonical"]) for spec in IMAGE_DROP_ACTION_ORDER_SPECS)
SETTING_VALUE_ALIASES = {tuple(key.split(".", 1)): value for key, value in _SETTINGS_SCHEMA["SETTING_VALUE_ALIASES"].items()}

DEFAULT_SETTINGS = _SETTINGS_SCHEMA["DEFAULT_SETTINGS"]
DEFAULT_SETTINGS["summary"].update(codex_model=SUMMARY_DEFAULT_CODEX_MODEL, codex_effort=SUMMARY_DEFAULT_CODEX_EFFORT, codex_service_tier=SUMMARY_DEFAULT_CODEX_SERVICE_TIER)
DEFAULT_SETTINGS["uploads"].update(max_bytes=UPLOAD_MAX_BYTES, filename_template=DEFAULT_UPLOAD_FILENAME_TEMPLATE)

LEGACY_YOAGENT_DEFAULTS = _SETTINGS_SCHEMA["LEGACY_YOAGENT_DEFAULTS"]
LEGACY_YOAGENT_PROMPT_MARKERS = _SETTINGS_SCHEMA["LEGACY_YOAGENT_PROMPT_MARKERS"]

SESSION_STATE_KEYS = set(_SETTINGS_SCHEMA["NOTIFY_TRANSITION_KEYS"]) - {value for value in _SETTINGS_SCHEMA["NOTIFY_TRANSITION_KEYS"] if value.startswith("pr-")}

# notifiable watched-PR transitions, opt-in alongside the session-state keys above.
PR_TRANSITION_KEYS = {value for value in _SETTINGS_SCHEMA["NOTIFY_TRANSITION_KEYS"] if value.startswith("pr-")}

# The full allowlist accepted in notifications.notify_transitions (session states + PR transitions).
NOTIFY_TRANSITION_KEYS = set(_SETTINGS_SCHEMA["NOTIFY_TRANSITION_KEYS"])

STALE_DEFAULT_MIGRATIONS = {tuple(key.split(".", 1)): tuple(value) if isinstance(value, list) else value for key, value in _SETTINGS_SCHEMA["STALE_DEFAULT_MIGRATIONS"].items()}

SETTING_LIMITS = {tuple(key.split(".", 1)): tuple(value) for key, value in _SETTINGS_SCHEMA["SETTING_LIMITS"].items()}

# String settings that accept an empty value (most strings revert to their default when blank).
STRING_ALLOW_EMPTY = {tuple(value.split(".", 1)) for value in _SETTINGS_SCHEMA["STRING_ALLOW_EMPTY"]}

SETTING_LIST_LIMITS = {tuple(key.split(".", 1)): value for key, value in _SETTINGS_SCHEMA["SETTING_LIST_LIMITS"].items()}

SETTING_CHOICES = {tuple(key.split(".", 1)): set(value) for key, value in _SETTINGS_SCHEMA["SETTING_CHOICES"].items()}

SETTING_PAYLOAD_CHOICE_ORDER = {tuple(key.split(".", 1)): tuple(value) for key, value in _SETTINGS_SCHEMA["SETTING_PAYLOAD_CHOICE_ORDER"].items()}

SETTING_HIDDEN_CHOICES = {tuple(key.split(".", 1)): set(value) for key, value in _SETTINGS_SCHEMA["SETTING_HIDDEN_CHOICES"].items()}

SETTING_COMMENTS = {tuple(key.split(".", 1)): value for key, value in _SETTINGS_SCHEMA["SETTING_COMMENTS"].items()}

SETTING_GUI_SECTIONS = {tuple(key.split(".", 1)): value for key, value in _SETTINGS_SCHEMA["SETTING_GUI_SECTIONS"].items()}

SETTING_GUI_SECTION_LOCALE_KEYS = _SETTINGS_SCHEMA["SETTING_GUI_SECTION_LOCALE_KEYS"]

SETTING_LOCALE_KEY_OVERRIDES = {tuple(key.split(".", 1)): value for key, value in _SETTINGS_SCHEMA["SETTING_LOCALE_KEY_OVERRIDES"].items()}

SETTING_WRITE_CONFIRMATION = {tuple(value.split(".", 1)) for value in _SETTINGS_SCHEMA["SETTING_WRITE_CONFIRMATION"]}

SETTING_SENSITIVITY = {tuple(key.split(".", 1)): value for key, value in _SETTINGS_SCHEMA["SETTING_SENSITIVITY"].items()}


def summary_settings(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    current = sanitize_settings(settings or default_settings())
    return dict(current["summary"])


def default_settings() -> dict[str, Any]:
    return copy.deepcopy(DEFAULT_SETTINGS)


def _pricing_profile_history(value: Any) -> list[tuple[float, str]]:
    """Return an ordered billing-profile timeline with adjacent repeats removed."""

    events: list[tuple[float, str]] = [(0.0, "default")]
    if isinstance(value, list):
        for item in value:
            timestamp_text, separator, profile_text = str(item or "").partition("|")
            if not separator:
                continue
            try:
                timestamp = float(timestamp_text)
            except ValueError:
                continue
            profile = profile_text.strip().lower()
            if not math.isfinite(timestamp) or timestamp < 0 or profile not in {"default", "subscription"}:
                continue
            events.append((timestamp, profile))
    by_timestamp: dict[float, str] = {}
    for timestamp, profile in events:
        by_timestamp[timestamp] = profile
    collapsed: list[tuple[float, str]] = []
    for timestamp, profile in sorted(by_timestamp.items()):
        if not collapsed or collapsed[-1][1] != profile:
            collapsed.append((timestamp, profile))
    return collapsed


def _pricing_profile_history_payload(value: Any) -> list[str]:
    return [f"{format(timestamp, '.17g')}|{profile}" for timestamp, profile in _pricing_profile_history(value)]


def _record_pricing_profile_changes(
    previous: dict[str, Any],
    current: dict[str, Any],
    *,
    effective_at: float,
) -> None:
    """Record profile changes in the settings document that owns the selection."""

    timestamp = float(effective_at)
    if not math.isfinite(timestamp) or timestamp < 0:
        timestamp = time.time()
    previous_cost = previous.get("cost") if isinstance(previous.get("cost"), dict) else {}
    current_cost = current.get("cost") if isinstance(current.get("cost"), dict) else {}
    for provider, history_key in _PRICING_PROFILE_HISTORY_KEYS.items():
        profile_key = f"{provider}_pricing_profile"
        previous_profile = previous_cost.get(profile_key, "default")
        current_profile = current_cost.get(profile_key, "default")
        history = _pricing_profile_history(previous_cost.get(history_key))
        if current_profile != previous_profile:
            history.append((timestamp, current_profile))
        current_cost[history_key] = _pricing_profile_history_payload([
            f"{event_at}|{profile}" for event_at, profile in history
        ])


def _usage_pricing_profile_eligible(
    settings: dict[str, Any] | None,
    *,
    provider: str,
    execution_source: str,
    endpoint: str,
) -> bool:
    provider_name = str(provider or "").strip().lower()
    source_name = str(execution_source or "").strip().lower().replace("!", "")
    endpoint_name = str(endpoint or "").strip().lower()
    yoagent_settings = settings.get("yoagent") if isinstance(settings, dict) else None
    yoagent_invocation = yoagent_settings.get("invocation", "cli") if isinstance(yoagent_settings, dict) else "cli"
    yoagent_cli = source_name == "yoagent" and endpoint_name == "yoagent" and yoagent_invocation == "cli"
    if provider_name == "openai":
        return (
            source_name == "codex"
            or yoagent_cli
            or (source_name in {"summary", "ai summary"} and endpoint_name in {"summary", "codex-exec"})
        )
    if provider_name == "anthropic":
        return source_name == "claude" or yoagent_cli
    return False


def usage_pricing_profile(
    settings: dict[str, Any] | None,
    *,
    provider: str,
    execution_source: str,
    endpoint: str = "",
    observed_at: float | None = None,
    requested_profile: str | None = None,
) -> str:
    """Return the effective profile for explicit subscription-eligible CLI usage."""

    provider_name = str(provider or "").strip().lower()
    if not _usage_pricing_profile_eligible(
        settings,
        provider=provider_name,
        execution_source=execution_source,
        endpoint=endpoint,
    ):
        return "default"
    if requested_profile is not None:
        requested = str(requested_profile or "").strip().lower()
        return requested if requested in {"default", "subscription"} else "default"
    cost_settings = settings.get("cost") if isinstance(settings, dict) else None
    if not isinstance(cost_settings, dict):
        return "default"
    if observed_at is None:
        value = cost_settings.get(f"{provider_name}_pricing_profile", "default")
        return value if value in {"default", "subscription"} else "default"
    try:
        timestamp = float(observed_at)
    except (TypeError, ValueError):
        return "default"
    if not math.isfinite(timestamp) or timestamp < 0:
        return "default"
    history_key = _PRICING_PROFILE_HISTORY_KEYS.get(provider_name)
    if history_key is None:
        return "default"
    if history_key not in cost_settings:
        # In-process callers and older test adapters may provide a minimal
        # settings snapshot. Real persisted settings are reconciled with an
        # effective boundary during read, so only the absent-key shape falls
        # back to its validated current selection.
        current = cost_settings.get(f"{provider_name}_pricing_profile", "default")
        return current if current in {"default", "subscription"} else "default"
    selected = "default"
    for effective_at, profile in _pricing_profile_history(cost_settings.get(history_key)):
        if effective_at > timestamp:
            break
        selected = profile
    return selected


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


def normalize_image_action_order_text(value: str) -> str:
    return " ".join(
        str(value or "")
        .strip()
        .removeprefix(";")
        .strip()
        .rstrip(".:")
        .lower()
        .split()
    )


def image_action_order_aliases(spec: dict[str, Any]) -> set[str]:
    label = str(spec.get("label") or "").strip()
    prompt = str(spec.get("prompt") or "").strip()
    aliases = {str(spec.get("id") or ""), str(spec.get("canonical") or ""), label}
    aliases.update(str(alias or "") for alias in spec.get("aliases", ()))
    if prompt:
        aliases.update({prompt, f"; {prompt}"})
        for label_alias in [label, *[str(alias or "") for alias in spec.get("aliases", ())]]:
            if label_alias:
                aliases.update({f"{label_alias}: {prompt}", f"{label_alias}: ; {prompt}"})
    return {normalize_image_action_order_text(alias) for alias in aliases if str(alias or "").strip()}


def canonical_image_action_order_item(value: str) -> str:
    normalized = normalize_image_action_order_text(value)
    if not normalized:
        return ""
    if normalized in {
        "insert-path",
        "insert path",
        "path",
        "server-ocr",
        "server ocr",
        "server ocr image",
        "ocr result",
        "shell-file",
        "show file type",
        "file",
        "file type",
    }:
        return ""
    for spec in IMAGE_DROP_ACTION_ORDER_SPECS:
        if normalized in image_action_order_aliases(spec):
            return str(spec["canonical"])
    raw = str(value or "").strip()
    colon_index = raw.find(":")
    if colon_index < 0:
        return raw
    prompt_text = normalize_image_action_order_text(raw[colon_index + 1 :])
    for spec in IMAGE_DROP_ACTION_ORDER_SPECS:
        prompt = normalize_image_action_order_text(str(spec.get("prompt") or ""))
        if prompt and prompt_text == prompt:
            return str(spec["canonical"])
    return raw


def coerce_image_action_order(value: Any, default: list[str]) -> list[str]:
    limit = SETTING_LIST_LIMITS[("uploads", "image_action_order")]
    items = coerce_string_list(value, default)
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        canonical = canonical_image_action_order_item(item)
        normalized = normalize_image_action_order_text(canonical)
        if not canonical or not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(canonical)
        if len(result) >= limit:
            break
    return result or list(default)


def migrate_stale_default(section: str, key: str, value: Any, default: Any) -> Any:
    stale_value = STALE_DEFAULT_MIGRATIONS.get((section, key))
    if stale_value is None:
        return value
    stale_values = stale_value if isinstance(stale_value, tuple) else (stale_value,)
    try:
        value_number = float(value)
    except (TypeError, ValueError):
        return value
    for candidate in stale_values:
        try:
            stale_number = float(candidate)
        except (TypeError, ValueError):
            continue
        if value_number == stale_number:
            return default
    return value


def sanitize_settings(raw: Any, coerced: list[str] | None = None) -> dict[str, Any]:
    # pass a `coerced` list to collect "<section>.<key>" for every PRESENT incoming value
    # that had to be clamped/reverted, so the API can report it instead of silently changing the value.
    defaults = default_settings()
    source = raw if isinstance(raw, dict) else {}
    sanitized = default_settings()
    for section, values in defaults.items():
        incoming = source.get(section, {})
        if not isinstance(incoming, dict):
            incoming = {}
        if section == "general" and "startup_tips" not in incoming and "startup_helpers" in incoming:
            incoming = dict(incoming)
            incoming["startup_tips"] = incoming["startup_helpers"]
        if section == "performance" and "workflow_transition_glow_seconds" not in incoming and "agent_window_cooldown_seconds" in incoming:
            incoming = dict(incoming)
            old_value = incoming.get("agent_window_cooldown_seconds")
            incoming["workflow_transition_glow_seconds"] = defaults["performance"]["workflow_transition_glow_seconds"] if old_value == 0 else old_value
        if section == "performance" and "workflow_transition_glow_seconds" not in incoming:
            legacy_value = source.get("appearance", {}).get("metadata_badge_pulse_seconds") if isinstance(source.get("appearance"), dict) else None
            if legacy_value is not None:
                incoming = dict(incoming)
                # The former 20-second default was not an explicit user choice; use the merged setting's 60-second default.
                incoming["workflow_transition_glow_seconds"] = defaults["performance"]["workflow_transition_glow_seconds"] if legacy_value == 20 else legacy_value
        for key, default in values.items():
            present = key in incoming
            value = incoming.get(key, default)
            if present:
                value = migrate_stale_default(section, key, value, default)
            if isinstance(value, str):
                value = SETTING_VALUE_ALIASES.get((section, key), {}).get(value.strip().lower(), value)
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
                if (section, key) == ("uploads", "image_action_order"):
                    items = coerce_image_action_order(value, default)
                elif section == "cost" and key in _PRICING_PROFILE_HISTORY_KEYS.values():
                    items = _pricing_profile_history_payload(value)
                else:
                    items = coerce_string_list(value, default)
                if (section, key) == ("notifications", "notify_transitions"):
                    items = [item for item in items if item in NOTIFY_TRANSITION_KEYS]
                limit = SETTING_LIST_LIMITS.get((section, key), 0)
                if limit > 0:
                    items = items[:limit]
                sanitized[section][key] = items
            elif (section, key) in SETTING_CHOICES:
                sanitized[section][key] = value if isinstance(value, str) and value in SETTING_CHOICES[(section, key)] else default
            elif isinstance(default, str):
                allow_empty = (section, key) in STRING_ALLOW_EMPTY
                valid = isinstance(value, str) and (allow_empty or value.strip())
                sanitized[section][key] = value if valid else default
            if coerced is not None and present and sanitized[section][key] != value:
                coerced.append(f"{section}.{key}")
    legacy_performance = source.get("performance") if isinstance(source.get("performance"), dict) else {}
    if any(key in legacy_performance for key in RETIRED_HOVER_TIMING_KEYS):
        sanitized["performance"]["popover_show_delay_ms"] = HOVER_SHOW_DELAY_MS
        sanitized["performance"]["popover_hide_delay_ms"] = HOVER_HIDE_DELAY_MS
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
            if section == "cost" and key in _PRICING_PROFILE_HISTORY_KEYS.values():
                continue
            if key in merged[section]:
                merged[section][key] = value
    merged = sanitize_settings(merged, coerced)
    appearance = merged["appearance"]
    appearance_patch = patch.get("appearance")
    if appearance["sync_font_sizes"] and isinstance(appearance_patch, dict) and (
        "sync_font_sizes" in appearance_patch or "global_font_size" in appearance_patch
    ):
        global_size = int(appearance["global_font_size"])
        finder_size = max(1, global_size - max(1, round(global_size * 0.12)))
        for key in ("ui_font_size", "terminal_font_size", "editor_font_size", "preview_font_size"):
            appearance[key] = global_size
        appearance["file_explorer_font_size"] = finder_size
    return merged


def settings_template(settings: dict[str, Any]) -> str:
    def encode_yaml_value(value: Any) -> str:
        # width=4096 keeps long scalars (e.g. the yoagent prompt) on one line so rejoining the
        # dumped lines with a space cannot corrupt folded text; allow_unicode keeps em-dashes literal.
        lines = [line for line in yaml.safe_dump(value, default_flow_style=True, width=4096, allow_unicode=True).splitlines() if line.strip() != "..."]
        return " ".join(line.strip() for line in lines).strip() or "''"

    lines = [
        "# YOmux user preferences.",
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
    with shared_config_lock(path):
        yield


def _write_settings_file_unlocked(settings: dict[str, Any], path: Path = SETTINGS_PATH) -> None:
    atomic_write_text(path, settings_template(sanitize_settings(settings)), mode=0o600)


def write_settings_file(settings: dict[str, Any], path: Path = SETTINGS_PATH) -> None:
    with locked_settings_file(path):
        signature = _settings_payload_file_signature(path)
        cached = _SETTINGS_PAYLOAD_CACHE.get(path)
        if signature is not None and cached is not None and cached[:2] == signature:
            previous = copy.deepcopy(cached[2]["settings"])
        else:
            previous, _ = _read_settings_file_unlocked(path)
        current = sanitize_settings(settings)
        _record_pricing_profile_changes(previous, current, effective_at=time.time())
        _write_settings_file_unlocked(current, path)
        _settings_payload_cache_clear_unlocked(path)


def _read_settings_file_unlocked(path: Path = SETTINGS_PATH, *, stored_values: dict[str, Any] | None = None) -> tuple[dict[str, Any], str]:
    if not path.exists():
        _write_settings_file_unlocked(default_settings(), path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        return default_settings(), str(exc)
    if stored_values is not None and isinstance(raw, dict):
        stored_values.update(raw)
    settings = sanitize_settings(raw)
    represented = copy.deepcopy(settings)
    represented_cost = represented["cost"]
    for provider, history_key in _PRICING_PROFILE_HISTORY_KEYS.items():
        represented_cost[f"{provider}_pricing_profile"] = _pricing_profile_history(
            represented_cost.get(history_key)
        )[-1][1]
    previous_history = {
        key: list(settings["cost"][key])
        for key in _PRICING_PROFILE_HISTORY_KEYS.values()
    }
    effective_at = path.stat().st_mtime if path.exists() else time.time()
    _record_pricing_profile_changes(represented, settings, effective_at=effective_at)
    if any(settings["cost"][key] != previous_history[key] for key in previous_history):
        # Hand edits are part of the supported settings contract. Persist the
        # inferred boundary once so a restart classifies delayed scans identically.
        _write_settings_file_unlocked(settings, path)
    return settings, ""


def read_settings_file(path: Path = SETTINGS_PATH) -> tuple[dict[str, Any], str]:
    if path.exists():
        return _read_settings_file_unlocked(path)
    with locked_settings_file(path):
        return _read_settings_file_unlocked(path)


def stats_prune_local_time(path: Path = SETTINGS_PATH) -> str:
    """Return the configured nightly YO!stats cleanup time for the statsd daemon.

    statsd is a separate process from the server, so it reads the preference file
    itself rather than being told. A missing or unreadable file resolves to the
    default; it never resolves to "do not prune", because a cleanup that silently
    stops is invisible until the disk fills.
    """

    if not path.exists():
        return DEFAULT_PRUNE_LOCAL_TIME
    settings, error = read_settings_file(path)
    if error:
        return DEFAULT_PRUNE_LOCAL_TIME
    return str(settings["stats"]["prune_at_local_time"])


def _settings_payload_unlocked(path: Path = SETTINGS_PATH) -> dict[str, Any]:
    stored_values: dict[str, Any] = {}
    settings, error = _read_settings_file_unlocked(path, stored_values=stored_values)
    stat = path.stat() if path.exists() else None
    if error:
        return {
            "settings": None,
            "defaults": default_settings(),
            "choices": settings_payload_choices(),
            "catalog": settings_catalog(),
            "path": str(path),
            "display_path": SETTINGS_DISPLAY_PATH,
            "mtime_ns": stat.st_mtime_ns if stat else 0,
            "error": error,
        }
    return {
        "settings": settings,
        # Effective defaults cannot tell a legacy browser whether the user has
        # explicitly selected Normal. Keep that provenance from the same YAML read.
        "tabber_mode_explicit": isinstance(stored_values.get("general"), dict) and "default_tabber_mode" in stored_values["general"],
        "defaults": default_settings(),
        "choices": settings_payload_choices(),
        "catalog": settings_catalog(settings),
        "path": str(path),
        "display_path": SETTINGS_DISPLAY_PATH,
        "mtime_ns": stat.st_mtime_ns if stat else 0,
        "error": error,
    }


def _settings_payload_file_signature(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat() if path.exists() else None
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size) if stat is not None else None


def _settings_payload_cache_clear_unlocked(path: Path = SETTINGS_PATH) -> None:
    _SETTINGS_PAYLOAD_CACHE.pop(path, None)


def _settings_payload_cache_store_unlocked(path: Path, payload: dict[str, Any]) -> None:
    signature = _settings_payload_file_signature(path)
    if signature is None:
        _settings_payload_cache_clear_unlocked(path)
        return
    _SETTINGS_PAYLOAD_CACHE[path] = (signature[0], signature[1], copy.deepcopy(payload))


def _settings_payload_cached_unlocked(path: Path = SETTINGS_PATH) -> dict[str, Any]:
    signature = _settings_payload_file_signature(path)
    cached = _SETTINGS_PAYLOAD_CACHE.get(path)
    if signature is not None and cached is not None and cached[:2] == signature:
        return copy.deepcopy(cached[2])
    payload = _settings_payload_unlocked(path)
    _settings_payload_cache_store_unlocked(path, payload)
    return copy.deepcopy(payload)


def settings_payload_choices() -> dict[str, list[str]]:
    return {
        f"{section}.{key}": list(choices)
        for (section, key), choices in SETTING_PAYLOAD_CHOICE_ORDER.items()
    }


def setting_value_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int) and not isinstance(value, bool):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, list):
        return "list"
    return "string"


def setting_units(section: str, key: str, default: Any) -> str:
    if (section, key) == ("uploads", "max_bytes"):
        return "bytes"
    if key.endswith("_ms") or key == "dir_cache_ms":
        return "milliseconds"
    if key.endswith("_seconds") or key.endswith("_interval_seconds"):
        return "seconds"
    if key.endswith("_days"):
        return "days"
    if key.endswith("_font_size") or key in {"tab_width", "pane_spacing", "image_preview_max_px"}:
        return "pixels"
    if key.endswith("_opacity"):
        return "percent"
    if key == "max_tabs_per_pane":
        return "tabs"
    if key == "scrollback":
        return "lines"
    return ""


def setting_all_choices_for_catalog(section: str, key: str) -> list[str]:
    ordered = SETTING_PAYLOAD_CHOICE_ORDER.get((section, key))
    if ordered is not None:
        return list(ordered)
    choices = SETTING_CHOICES.get((section, key))
    if not choices:
        return []
    return sorted(choices)


def setting_choices_for_catalog(section: str, key: str) -> list[str]:
    hidden = SETTING_HIDDEN_CHOICES.get((section, key), set())
    return [choice for choice in setting_all_choices_for_catalog(section, key) if choice not in hidden]


def setting_aliases_for_catalog(section: str, key: str) -> dict[str, str]:
    return dict(SETTING_VALUE_ALIASES.get((section, key), {}))


def setting_choice_labels_for_catalog(section: str, key: str) -> dict[str, str]:
    if (section, key) in {("yoagent", "codex_model"), ("summary", "codex_model")}:
        return {model_id: str(spec["display_name"]) for model_id, spec in YOAGENT_CODEX_MODEL_CATALOG.items()}
    if (section, key) == ("yoagent", "claude_model"):
        return {model_id: str(spec["display_name"]) for model_id, spec in _SETTINGS_SCHEMA["YOAGENT_CLAUDE_MODEL_CATALOG"].items()}
    return {}


def setting_choice_metadata_for_catalog(section: str, key: str) -> dict[str, dict[str, Any]]:
    if (section, key) in {("yoagent", "codex_model"), ("summary", "codex_model")}:
        return {
            model_id: {
                "display_name": str(spec["display_name"]),
                "default_effort": str(spec["default_effort"]),
                "effort_options": list(spec["effort_options"]),
            }
            for model_id, spec in YOAGENT_CODEX_MODEL_CATALOG.items()
        }
    return {}


def setting_catalog_label(section: str, key: str) -> str:
    if (section, key) == ("uploads", "max_bytes"):
        return "File transfer size cap"
    return key.replace("_", " ").strip().capitalize()


def settings_catalog(settings: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    current = sanitize_settings(settings or default_settings())
    defaults = default_settings()
    catalog: dict[str, dict[str, Any]] = {}
    for section, values in defaults.items():
        for key, default in values.items():
            if section == "cost" and key in _PRICING_PROFILE_HISTORY_KEYS.values():
                continue
            path = f"{section}.{key}"
            lower_upper = SETTING_LIMITS.get((section, key))
            limits = {"min": lower_upper[0], "max": lower_upper[1]} if lower_upper else None
            list_limit = SETTING_LIST_LIMITS.get((section, key))
            locale_keys = {
                "description": f"pref.{path}.help",
                "label": f"pref.{path}.label",
                **SETTING_LOCALE_KEY_OVERRIDES.get((section, key), {}),
            }
            catalog[path] = {
                "path": path,
                "section": section,
                "key": key,
                "label": setting_catalog_label(section, key),
                "locale_keys": locale_keys,
                "current": current.get(section, {}).get(key, default),
                "default": default,
                "type": setting_value_type(default),
                "choices": setting_choices_for_catalog(section, key),
                "accepted_choices": setting_all_choices_for_catalog(section, key),
                "hidden_choices": sorted(SETTING_HIDDEN_CHOICES.get((section, key), set())),
                "choice_labels": setting_choice_labels_for_catalog(section, key),
                "choice_metadata": setting_choice_metadata_for_catalog(section, key),
                "limits": limits,
                "units": setting_units(section, key, default),
                "empty_allowed": (section, key) in STRING_ALLOW_EMPTY,
                "list_limit": list_limit,
                "aliases": setting_aliases_for_catalog(section, key),
                "description": SETTING_COMMENTS.get((section, key), ""),
                "sensitivity": SETTING_SENSITIVITY.get((section, key), "normal"),
                "requires_confirmation": (section, key) in SETTING_WRITE_CONFIRMATION,
                "read_role": "readonly",
                "write_role": "admin",
                "live_apply": "next-use" if section == "yoagent" and key in {"system_prompt", "intro", "format"} else "live",
                "gui": {
                    "section": SETTING_GUI_SECTIONS.get((section, key), ""),
                    "section_locale_key": SETTING_GUI_SECTION_LOCALE_KEYS.get(SETTING_GUI_SECTIONS.get((section, key), ""), ""),
                    "visible": (section, key) in SETTING_GUI_SECTIONS,
                },
            }
    return catalog


def settings_payload(path: Path = SETTINGS_PATH) -> dict[str, Any]:
    if path.exists():
        return _settings_payload_cached_unlocked(path)
    with locked_settings_file(path):
        return _settings_payload_cached_unlocked(path)


def save_settings(patch: Any, path: Path = SETTINGS_PATH) -> dict[str, Any]:
    with locked_settings_file(path):
        current, error = _read_settings_file_unlocked(path)
        if error:
            raise ValueError(f"settings file could not be loaded: {error}")
        coerced: list[str] = []
        next_settings = merge_settings(current, patch, coerced)
        _record_pricing_profile_changes(current, next_settings, effective_at=time.time())
        _write_settings_file_unlocked(next_settings, path)
        payload = _settings_payload_unlocked(path)
        _settings_payload_cache_store_unlocked(path, payload)
        payload = copy.deepcopy(payload)
        # report which patched keys were clamped/reverted so the API/UI can surface it
        # instead of silently changing the value (e.g. ui_font_size:999 -> 30).
        payload["coerced"] = coerced
        return payload
