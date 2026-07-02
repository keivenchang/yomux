# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""YO!agent skill discovery and validation."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
import re
from typing import Any

import yaml

from ..atomic_file import atomic_write_text
from ..atomic_file import file_lock
from ..common import CONFIG_DIR
from ..common import truncate_text
from ..locales import message_fields
from ..locales import user_message_payload


YOAGENT_PACKAGE = "yolomux_lib.yoagent"
BUILTIN_SKILLS_DIR = "builtin_skills"
BUILTIN_CONTEXT_DIR = "builtin_context"
USER_SKILLS_DIR = CONFIG_DIR / "skills.d"
USER_CONTEXT_DIR = CONFIG_DIR / "context.d"
YOAGENT_SKILL_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
YOAGENT_SKILL_TEXT_LIMIT = 1200
YOAGENT_CONTEXT_TEXT_LIMIT = 2400
YOAGENT_USER_FILE_TEXT_LIMIT = 64 * 1024

YOAGENT_SKILL_TOOLS = frozenset({
    "read_activity",
    "read_settings_catalog",
    "read_product_capabilities",
    "recommend_next_work",
    "watch_session",
    "watch_all_sessions",
    "notify_user",
    "read_skill_files",
    "write_skill_file",
    "delete_skill_file",
    "write_settings_patch",
    "preview_send_prompt",
    "execute_confirmed_send",
    "summarize_sessions",
})


class YoagentSkillValidationError(ValueError):
    def __init__(self, errors: list[dict[str, Any]]):
        self.errors = errors
        super().__init__("; ".join(str(item.get("error") or "") for item in errors if item.get("error")))


def skill_error(source: str, key: str, fallback: str, params: dict[str, Any] | None = None, *, diagnostic: str = "") -> dict[str, Any]:
    return {
        "source": source,
        "diagnostic": diagnostic,
        **message_fields("error", key, fallback, params),
    }


def skill_validation_payload(error: YoagentSkillValidationError) -> dict[str, Any]:
    primary = error.errors[0] if error.errors else skill_error("", "yoagent.skill.error.invalid", "Invalid skill file.")
    params = primary.get("error_params") if isinstance(primary.get("error_params"), dict) else {}
    return {
        "validation_errors": error.errors,
        **user_message_payload(str(primary.get("error_key") or "yoagent.skill.error.invalid"), str(primary.get("error") or error), **params),
    }


@dataclass(frozen=True)
class YoagentSkill:
    name: str
    kind: str
    description: str
    tools: tuple[str, ...]
    triggers: tuple[str, ...]
    enabled: bool
    builtin: bool
    source: str
    confirmation: str
    default_timeout_minutes: int | None

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name,
            "kind": self.kind,
            "description": self.description,
            "tools": list(self.tools),
            "triggers": list(self.triggers),
            "enabled": self.enabled,
            "builtin": self.builtin,
            "source": self.source,
            "confirmation": self.confirmation,
        }
        if self.default_timeout_minutes is not None:
            payload["default_timeout_minutes"] = self.default_timeout_minutes
        return payload

    def context_line(self) -> str:
        tool_text = ", ".join(self.tools)
        tools = f"; tools: {tool_text}" if tool_text else ""
        return f"YO!agent skill `{self.name}` ({self.kind}): {self.description}{tools}"


def user_skill_dirs(config_dir: Path | None = None) -> dict[str, str]:
    root = config_dir or CONFIG_DIR
    return {
        "root": str(root),
        "skills": str(root / "skills.d"),
        "context": str(root / "context.d"),
    }


def user_yoagent_dirs(config_dir: Path | None = None) -> dict[str, str]:
    return user_skill_dirs(config_dir)


def builtin_resource_label(dirname: str, name: str) -> str:
    return f"yolomux_lib/yoagent/{dirname}/{name}"


def normalize_user_file_kind(kind: str) -> str:
    value = str(kind or "").strip().lower()
    if value in {"skill", "skills", "yaml", "yml"}:
        return "skill"
    if value in {"context", "contexts", "md", "markdown"}:
        return "context"
    raise YoagentSkillValidationError([skill_error("", "yoagent.skill.error.kind", "File type must be one of: skill, context.")])


def clean_user_file_name(name: str, suffix: str) -> str:
    raw = str(name or "").strip()
    if not raw:
        raise YoagentSkillValidationError([skill_error("", "yoagent.skill.error.nameRequired", "A file name is required.")])
    if "/" in raw or "\\" in raw or raw != Path(raw).name:
        raise YoagentSkillValidationError([skill_error("", "yoagent.skill.error.nameFileOnly", "The name must be a file name, not a path.")])
    if raw.endswith(suffix):
        raw = raw[: -len(suffix)]
    if not YOAGENT_SKILL_NAME_RE.fullmatch(raw):
        raise YoagentSkillValidationError([skill_error("", "yoagent.skill.error.namePattern", "The name must match `[a-z][a-z0-9-]{1,63}`.")])
    return raw


def user_skill_file_path(kind: str, name: str, config_dir: Path | None = None) -> tuple[str, Path]:
    normalized = normalize_user_file_kind(kind)
    root = config_dir or CONFIG_DIR
    if normalized == "skill":
        suffix = ".yaml"
        directory = root / "skills.d"
    else:
        suffix = ".md"
        directory = root / "context.d"
    stem = clean_user_file_name(name, suffix)
    return normalized, directory / f"{stem}{suffix}"


def skill_file_payload(kind: str, path: Path, text: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "kind": kind,
        "name": path.stem,
        "path": str(path),
        "text": text,
        "exists": path.exists(),
    }
    if kind == "skill":
        skill, errors = parse_skill_text(text, str(path), False, path.stem)
        payload["valid"] = skill is not None and not errors
        payload["errors"] = errors
        if skill is not None:
            payload["skill"] = skill.to_payload()
    return payload


def read_user_skill_file(kind: str, name: str, config_dir: Path | None = None) -> dict[str, Any]:
    normalized, path = user_skill_file_path(kind, name, config_dir)
    text = path.read_text(encoding="utf-8")
    return skill_file_payload(normalized, path, text)


def list_user_skill_files(config_dir: Path | None = None) -> dict[str, Any]:
    dirs = user_skill_dirs(config_dir)
    files: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for child in path_skill_files(Path(dirs["skills"])):
        try:
            files.append(skill_file_payload("skill", child, child.read_text(encoding="utf-8")))
        except OSError as exc:
            diagnostic = str(exc)
            errors.append(skill_error(str(child), "yoagent.skill.error.readFailed", f"Could not read `{child}`: {diagnostic}", {"source": str(child), "error": diagnostic}, diagnostic=diagnostic))
    for child in path_markdown_files(Path(dirs["context"])):
        try:
            files.append(skill_file_payload("context", child, child.read_text(encoding="utf-8")))
        except OSError as exc:
            diagnostic = str(exc)
            errors.append(skill_error(str(child), "yoagent.skill.error.readFailed", f"Could not read `{child}`: {diagnostic}", {"source": str(child), "error": diagnostic}, diagnostic=diagnostic))
    return {"ok": not errors, "user_dirs": dirs, "files": files, "errors": errors}


def validate_user_skill_file_text(kind: str, path: Path, text: str) -> None:
    if len(text) > YOAGENT_USER_FILE_TEXT_LIMIT:
        raise YoagentSkillValidationError([skill_error(str(path), "yoagent.skill.error.textTooLarge", f"Text must be at most {YOAGENT_USER_FILE_TEXT_LIMIT} characters.", {"limit": YOAGENT_USER_FILE_TEXT_LIMIT})])
    if kind == "skill":
        skill, errors = parse_skill_text(text, str(path), False, path.stem)
        if errors:
            raise YoagentSkillValidationError(errors)
        if skill is None:
            raise YoagentSkillValidationError([skill_error(str(path), "yoagent.skill.error.invalid", "Invalid skill file.")])
        if skill.name != path.stem:
            raise YoagentSkillValidationError([skill_error(str(path), "yoagent.skill.error.nameMismatch", "The skill name must match the file name.")])
    elif not text.strip():
        raise YoagentSkillValidationError([skill_error(str(path), "yoagent.skill.error.contextRequired", "Context text is required.")])


def write_user_skill_file(kind: str, name: str, text: str, config_dir: Path | None = None) -> dict[str, Any]:
    normalized, path = user_skill_file_path(kind, name, config_dir)
    clean_text = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    validate_user_skill_file_text(normalized, path, clean_text)
    with file_lock(path, dir_mode=0o700):
        atomic_write_text(path, clean_text.rstrip() + "\n", mode=0o600)
    return read_user_skill_file(normalized, path.name, config_dir)


def delete_user_skill_file(kind: str, name: str, config_dir: Path | None = None) -> dict[str, Any]:
    normalized, path = user_skill_file_path(kind, name, config_dir)
    with file_lock(path, dir_mode=0o700):
        path.unlink()
    return {"ok": True, "kind": normalized, "name": path.stem, "path": str(path), "deleted": True}


def resource_children(dirname: str) -> list[Traversable]:
    try:
        base = resources.files(YOAGENT_PACKAGE).joinpath(dirname)
        return sorted((child for child in base.iterdir() if child.is_file()), key=lambda child: child.name)
    except (FileNotFoundError, ModuleNotFoundError, NotADirectoryError):
        return []


def yaml_resource_children(dirname: str) -> list[Traversable]:
    return [child for child in resource_children(dirname) if child.name.endswith((".yaml", ".yml"))]


def markdown_resource_children(dirname: str) -> list[Traversable]:
    return [child for child in resource_children(dirname) if child.name.endswith(".md")]


def path_skill_files(path: Path) -> list[Path]:
    try:
        return sorted(child for child in path.iterdir() if child.is_file() and child.suffix.lower() in {".yaml", ".yml"})
    except OSError:
        return []


def path_markdown_files(path: Path) -> list[Path]:
    try:
        return sorted(child for child in path.iterdir() if child.is_file() and child.suffix.lower() == ".md")
    except OSError:
        return []


def clean_string(value: Any, limit: int = YOAGENT_SKILL_TEXT_LIMIT) -> str:
    return truncate_text(str(value or "").strip(), limit)


def clean_string_list(value: Any, field: str, errors: list[dict[str, Any]], source: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        errors.append(skill_error(source, "yoagent.skill.error.listOfStrings", f"{field} must be a list of strings.", {"field": field}))
        return ()
    result: list[str] = []
    for item in value:
        text = clean_string(item, 160)
        if not text:
            continue
        if text not in result:
            result.append(text)
    return tuple(result)


def clean_timeout_minutes(value: Any, errors: list[dict[str, Any]], source: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        errors.append(skill_error(source, "yoagent.skill.error.timeoutInteger", "default_timeout_minutes must be an integer."))
        return None
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        errors.append(skill_error(source, "yoagent.skill.error.timeoutInteger", "default_timeout_minutes must be an integer."))
        return None
    if minutes < 1 or minutes > 24 * 60:
        errors.append(skill_error(source, "yoagent.skill.error.timeoutRange", "default_timeout_minutes must be between 1 and 1440.", {"min": 1, "max": 1440}))
        return None
    return minutes


def parse_skill_mapping(raw: Any, source: str, builtin: bool, fallback_name: str) -> tuple[YoagentSkill | None, list[dict[str, Any]]]:
    if not isinstance(raw, dict):
        return None, [skill_error(source, "yoagent.skill.error.mappingRequired", "The skill file must contain a YAML mapping.")]
    errors: list[dict[str, Any]] = []
    name = clean_string(raw.get("name") or fallback_name, 80)
    if not YOAGENT_SKILL_NAME_RE.fullmatch(name):
        errors.append(skill_error(source, "yoagent.skill.error.namePattern", "The name must match `[a-z][a-z0-9-]{1,63}`."))
    enabled_value = raw.get("enabled", True)
    if not isinstance(enabled_value, bool):
        errors.append(skill_error(source, "yoagent.skill.error.enabledBoolean", "enabled must be true or false."))
        enabled = True
    else:
        enabled = enabled_value
    kind = clean_string(raw.get("kind") or "workflow", 80)
    if not YOAGENT_SKILL_NAME_RE.fullmatch(kind):
        errors.append(skill_error(source, "yoagent.skill.error.kindPattern", "kind must match `[a-z][a-z0-9-]{1,63}`."))
    description = clean_string(raw.get("description"), YOAGENT_SKILL_TEXT_LIMIT)
    if enabled and not description:
        errors.append(skill_error(source, "yoagent.skill.error.descriptionRequired", "A description is required when enabled is true."))
    tools = clean_string_list(raw.get("tools"), "tools", errors, source)
    unknown_tools = [tool for tool in tools if tool not in YOAGENT_SKILL_TOOLS]
    if unknown_tools:
        tools_text = ", ".join(unknown_tools)
        errors.append(skill_error(source, "yoagent.skill.error.unknownTools", f"Unknown tools: {tools_text}.", {"tools": tools_text}))
    triggers = clean_string_list(raw.get("triggers"), "triggers", errors, source)
    confirmation = clean_string(raw.get("confirmation") or "none", 80)
    if confirmation not in {"none", "required", "template"}:
        errors.append(skill_error(source, "yoagent.skill.error.confirmation", "confirmation must be one of: none, required, template."))
    timeout = clean_timeout_minutes(raw.get("default_timeout_minutes"), errors, source)
    if errors:
        return None, errors
    return YoagentSkill(
        name=name,
        kind=kind,
        description=description,
        tools=tools,
        triggers=triggers,
        enabled=enabled,
        builtin=builtin,
        source=source,
        confirmation=confirmation,
        default_timeout_minutes=timeout,
    ), []


def parse_skill_text(text: str, source: str, builtin: bool, fallback_name: str) -> tuple[YoagentSkill | None, list[dict[str, Any]]]:
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        diagnostic = str(exc)
        return None, [skill_error(source, "yoagent.skill.error.invalidYaml", f"Invalid YAML: {diagnostic}", {"error": diagnostic}, diagnostic=diagnostic)]
    return parse_skill_mapping(raw, source, builtin, fallback_name)


def load_builtin_skills() -> tuple[list[YoagentSkill], list[dict[str, Any]]]:
    skills: list[YoagentSkill] = []
    errors: list[dict[str, Any]] = []
    for child in yaml_resource_children(BUILTIN_SKILLS_DIR):
        source = builtin_resource_label(BUILTIN_SKILLS_DIR, child.name)
        skill, item_errors = parse_skill_text(child.read_text(encoding="utf-8"), source, True, Path(child.name).stem)
        errors.extend(item_errors)
        if skill is not None:
            skills.append(skill)
    return skills, errors


def load_user_skills(path: Path | None = None) -> tuple[list[YoagentSkill], list[dict[str, Any]]]:
    skills: list[YoagentSkill] = []
    errors: list[dict[str, Any]] = []
    for child in path_skill_files(path or USER_SKILLS_DIR):
        source = str(child)
        try:
            text = child.read_text(encoding="utf-8")
        except OSError as exc:
            diagnostic = str(exc)
            errors.append(skill_error(source, "yoagent.skill.error.readFailed", f"Could not read `{source}`: {diagnostic}", {"source": source, "error": diagnostic}, diagnostic=diagnostic))
            continue
        skill, item_errors = parse_skill_text(text, source, False, child.stem)
        errors.extend(item_errors)
        if skill is not None:
            skills.append(skill)
    return skills, errors


def load_context_texts(user_context_dir: Path | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    contexts: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for child in markdown_resource_children(BUILTIN_CONTEXT_DIR):
        source = builtin_resource_label(BUILTIN_CONTEXT_DIR, child.name)
        text = clean_string(child.read_text(encoding="utf-8"), YOAGENT_CONTEXT_TEXT_LIMIT)
        if text:
            contexts.append({"name": Path(child.name).stem, "source": source, "builtin": True, "text": text})
    for child in path_markdown_files(user_context_dir or USER_CONTEXT_DIR):
        source = str(child)
        try:
            text = clean_string(child.read_text(encoding="utf-8"), YOAGENT_CONTEXT_TEXT_LIMIT)
        except OSError as exc:
            diagnostic = str(exc)
            errors.append(skill_error(source, "yoagent.skill.error.readFailed", f"Could not read `{source}`: {diagnostic}", {"source": source, "error": diagnostic}, diagnostic=diagnostic))
            continue
        if text:
            contexts.append({"name": child.stem, "source": source, "builtin": False, "text": text})
    return contexts, errors


def overlay_skills(builtin: list[YoagentSkill], user: list[YoagentSkill]) -> list[YoagentSkill]:
    merged: dict[str, YoagentSkill] = {}
    for skill in builtin:
        merged[skill.name] = skill
    for skill in user:
        merged[skill.name] = skill
    return list(merged.values())


def load_yoagent_skills(
    user_skills_dir: Path | None = None,
    user_context_dir: Path | None = None,
    config_dir: Path | None = None,
) -> dict[str, Any]:
    builtin_skills, builtin_errors = load_builtin_skills()
    user_skills, user_errors = load_user_skills(user_skills_dir or ((config_dir or CONFIG_DIR) / "skills.d"))
    contexts, context_errors = load_context_texts(user_context_dir or ((config_dir or CONFIG_DIR) / "context.d"))
    skills = overlay_skills(builtin_skills, user_skills)
    enabled_skills = [skill for skill in skills if skill.enabled]
    context_lines = [skill.context_line() for skill in enabled_skills]
    for item in contexts:
        name = str(item.get("name") or "context")
        text = str(item.get("text") or "").strip()
        if text:
            context_lines.append(f"YO!agent context `{name}`: {text}")
    dirs = user_yoagent_dirs(config_dir)
    return {
        "ok": not (builtin_errors or user_errors or context_errors),
        "builtin_dirs": {
            "skills": builtin_resource_label(BUILTIN_SKILLS_DIR, ""),
            "context": builtin_resource_label(BUILTIN_CONTEXT_DIR, ""),
        },
        "user_dirs": dirs,
        "allowed_tools": sorted(YOAGENT_SKILL_TOOLS),
        "skills": [skill.to_payload() for skill in skills],
        "contexts": contexts,
        "context_lines": context_lines,
        "errors": [*builtin_errors, *user_errors, *context_errors],
    }
